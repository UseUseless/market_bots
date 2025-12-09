"""
Клиент для взаимодействия с Tinkoff Invest API (Т-Инвестиции).

Этот модуль реализует адаптер для работы с Московской биржей через API Тинькофф.

Особенности реализации:
1.  **FIGI Resolver**: Автоматически конвертирует тикеры (SBER) в идентификаторы FIGI,
    которые требуются для API Тинькофф.
2.  **Parallel Scanning**: Использует ThreadPoolExecutor для быстрого сканирования
    рынка (расчет оборота) без нарушения лимитов API.
3.  **Safety**: Работает с токенами ReadOnly и FullAccess, разделяя права.
"""

import logging
import time
from datetime import timedelta
from typing import List, Dict, Any, Optional
import concurrent.futures

from tqdm import tqdm
import pandas as pd

from tinkoff.invest import (
    Client, RequestError,
    CandleInterval, InstrumentStatus
)
from tinkoff.invest.utils import now, quotation_to_decimal

from app.infrastructure.exchanges.base import ExchangeExchangeHandler
from app.shared.primitives import ExchangeType
from app.shared.config import config

logger = logging.getLogger(__name__)


class TinkoffHandler(ExchangeExchangeHandler):
    """
    Адаптер для биржи Tinkoff Invest.
    Работает в режиме Read-Only.
    """

    def __init__(self):
        """
        Инициализирует клиента.
        """
        super().__init__()

        self.token = config.TINKOFF_TOKEN_READONLY

        if not self.token or "Your" in self.token:
            raise ConnectionError(
                "TINKOFF_TOKEN_READONLY не задан в .env. Для работы необходим токен (можно только для чтения).")

        logging.info(f"Tinkoff Client инициализирован (Token: READONLY).")
        self._check_token()

    def _check_token(self) -> bool:
        """
        Проверяет валидность токена ReadOnly простым запросом к API.

        Returns:
            bool: True, если токен валиден.

        Raises:
            ConnectionAbortedError: Если токен недействителен.
        """
        try:
            with Client(self.token) as client:
                # Простейший запрос - получение статуса сервисов или счетов
                client.instruments.shares(instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE)
            return True
        except RequestError as e:
            logging.critical(f"Ошибка проверки токена Tinkoff: {e}")
            raise ConnectionAbortedError(f"Токен невалиден: {e.details}")

    def _resolve_figi(self, instrument: str) -> str:
        """
        Конвертирует человекочитаемый тикер (SBER) в системный ID (FIGI).

        Args:
            instrument (str): Тикер или FIGI.

        Returns:
            str: Валидный FIGI.
        """
        # Если это уже FIGI (начинается с BBG...), возвращаем как есть
        if instrument.startswith("BBG"):
            return instrument

        logger.info(f"Поиск FIGI для инструмента '{instrument}'...")
        with Client(self.token) as c:
            found = c.instruments.find_instrument(query=instrument)
            if not found.instruments:
                raise ValueError(f"Инструмент '{instrument}' не найден в API Тинькофф.")

            instrument_upper = instrument.upper()
            # Берем класс инструмента по умолчанию из конфига (обычно TQBR для акций РФ)
            class_code = config.EXCHANGE_SPECIFIC_CONFIG[ExchangeType.TINKOFF]['DEFAULT_CLASS_CODE']

            # 1. Строгий поиск: совпадение тикера и класса
            match = next((i for i in found.instruments
                          if i.ticker == instrument_upper and i.class_code == class_code), None)

            # 2. Мягкий поиск (fallback): берем первый попавшийся с таким тикером
            target = match if match else found.instruments[0]

            return target.figi

    @staticmethod
    def _cast_money(money_value) -> float:
        """
        Конвертирует структуру Quotation (units, nano) в float.
        """
        return money_value.units + money_value.nano / 1e9

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """
        Скачивает исторические свечи.

        Args:
            instrument (str): Тикер.
            interval (str): Интервал (например, '1min').
            days (int): Глубина истории.

        Returns:
            pd.DataFrame: DataFrame с колонками OHLCV.
        """
        try:
            figi = self._resolve_figi(instrument)
        except Exception as e:
            logging.error(f"Ошибка получения FIGI: {e}")
            return pd.DataFrame()

        interval_name = config.EXCHANGE_INTERVAL_MAPS[ExchangeType.TINKOFF].get(interval)
        if not interval_name:
            logging.error(f"Интервал '{interval}' не поддерживается Тинькофф.")
            return pd.DataFrame()

        api_interval = getattr(CandleInterval, interval_name)
        all_candles = []
        start_date = now() - timedelta(days=days)

        try:
            with Client(self.token) as c, tqdm(total=days, desc=f"Tinkoff {instrument}", unit="d") as pbar:
                # get_all_candles автоматически обрабатывает пагинацию
                for candle in c.get_all_candles(figi=figi, from_=start_date, interval=api_interval):

                    all_candles.append({
                        "time": candle.time, # datetime object (aware)
                        "open": self._cast_money(candle.open),
                        "high": self._cast_money(candle.high),
                        "low": self._cast_money(candle.low),
                        "close": self._cast_money(candle.close),
                        "volume": candle.volume
                    })

                    # Упрощенное обновление прогресса (неточное, но визуально достаточное)
                    if len(all_candles) % 100 == 0:
                        pbar.update(0)

                pbar.update(days - pbar.n) # Завершаем прогресс-бар

        except RequestError as e:
            logging.error(f"Ошибка API при скачивании данных: {e}")
            return pd.DataFrame()

        # Используем метод базового класса для создания DF
        return self._process_candles_to_df(all_candles)

    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        """
        Получает параметры инструмента (шаг цены, лотность).
        """
        try:
            figi = self._resolve_figi(instrument)
            with Client(self.token) as client:
                instr = client.instruments.get_instrument_by(id_type=1, id=figi).instrument
                return {
                    "lot_size": instr.lot,
                    "min_price_increment": float(quotation_to_decimal(instr.min_price_increment)),
                    "qty_step": float(instr.lot)
                }
        except Exception:
            return {}

    def _calculate_single_turnover(self, share: Any, start: Any, end: Any) -> Optional[Dict[str, Any]]:
        """
        Вспомогательный метод для расчета оборота одной акции в отдельном потоке.

        Создает собственный экземпляр Client, так как gRPC-каналы не всегда
        корректно работают при конкурентном доступе из разных потоков.

        Args:
            share: Объект инструмента из SDK.
            start: Начало периода.
            end: Конец периода.

        Returns:
            Dict или None: Словарь {'ticker', 'turnover'} или None.
        """
        try:
            # Искусственная задержка для "размазывания" нагрузки при старте потоков
            time.sleep(0.2)

            with Client(self.token) as client:
                # Запрашиваем дневные свечи
                candles = client.market_data.get_candles(
                    figi=share.figi, from_=start, to=end,
                    interval=CandleInterval.CANDLE_INTERVAL_DAY
                ).candles

                if not candles:
                    return None

                # Считаем оборот: Сумма(Close * Volume * Lot)
                val = sum(self._cast_money(c.close) * c.volume * share.lot for c in candles)

                return {"ticker": share.ticker, "turnover": val} if val > 0 else None
        except:
            # Игнорируем ошибки (лимиты, отсутствие данных), чтобы не рушить весь батч
            return None

    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        """
        Возвращает список самых ликвидных акций (TQBR) по обороту.

        Использует ThreadPoolExecutor для параллельного опроса сотен инструментов.
        Это ускоряет процесс в разы по сравнению с последовательным перебором.

        Args:
            count (int): Кол-во инструментов в топе.

        Returns:
            List[str]: Список тикеров.
        """
        logger.info(f"Tinkoff: Сканирование топ-{count} ликвидных акций (TQBR)...")
        try:
            # 1. Получаем список всех доступных акций
            with Client(self.token) as client:
                shares = client.instruments.shares(
                    instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE
                ).instruments

                # Фильтруем: только акции Мосбиржи (TQBR), в рублях
                tqbr = [s for s in shares if s.class_code == 'TQBR' and s.currency == 'rub'
                        and s.buy_available_flag]

            stats = []
            s_date, e_date = now() - timedelta(days=2), now()

            # 2. Параллельный расчет оборота
            # max_workers=3 — безопасный лимит для предотвращения ошибки 429 Too Many Requests
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(self._calculate_single_turnover, s, s_date, e_date): s
                    for s in tqbr
                }

                for f in tqdm(concurrent.futures.as_completed(futures), total=len(tqbr), desc="Сканирование"):
                    res = f.result()
                    if res:
                        stats.append(res)

            # 3. Сортировка
            top = sorted(stats, key=lambda x: x['turnover'], reverse=True)[:count]
            return [x['ticker'] for x in top]

        except Exception as e:
            logger.error(f"Ошибка при сканировании ликвидности Tinkoff: {e}")
            return []