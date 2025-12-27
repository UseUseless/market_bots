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

import app.infrastructure.feeds.backtest.provider
from app.infrastructure.exchanges.base import ExchangeExchangeHandler
from app.shared.types import ExchangeType
from app.shared.config import config

logger = logging.getLogger(__name__)


class TinkoffHandler(ExchangeExchangeHandler):
    """
    Адаптер для взаимодействия с API Тинькофф Инвестиций.
    """
    def __init__(self):
        """Инициализирует клиент, проверяет наличие токена и устанавливает настройки по умолчанию.

        Raises:
            ConnectionError: Если токен не найден в конфигурации.
        """
        super().__init__()
        self.token = config.TINKOFF_TOKEN_READONLY
        if not self.token or "Your" in self.token:
            raise ConnectionError("TINKOFF_TOKEN_READONLY не задан.")
        
        # Кэш для настроек (класс по умолчанию)
        self.default_class = config.EXCHANGE_SPECIFIC_CONFIG[ExchangeType.TINKOFF].get('DEFAULT_CLASS_CODE', 'TQBR')
        
        logging.info(f"Tinkoff Client инициализирован.")
        self._check_token()

    def _check_token(self) -> bool:
        """
        Проверяет валидность токена путем выполнения тестового запроса.

        Returns:
            bool: True, если токен валиден.

        Raises:
            ConnectionAbortedError: Если токен невалиден или запрос завершился ошибкой.
        """
        try:
            with Client(self.token) as client:
                client.instruments.shares(instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE)
            return True
        except RequestError as e:
            logging.critical(f"Ошибка проверки токена Tinkoff: {e}")
            raise ConnectionAbortedError(f"Токен невалиден: {e.details}")

    def _find_instrument_obj(self, client: Client, ticker: str):
        """Ищет объект инструмента по тикеру, отдавая приоритет классу TQBR.

        Args:
            client (Client): Активный клиент API Tinkoff.
            ticker (str): Тикер инструмента (например, 'SBER').

        Returns:
            Optional[Instrument]: Объект инструмента с данными (figi, lot, min_step) или None, если не найден.
        """
        try:
            found = client.instruments.find_instrument(query=ticker)
            if not found.instruments:
                return None

            ticker_upper = ticker.upper()
            
            # 1. Строгий поиск (Тикер + Класс TQBR)
            match = next((i for i in found.instruments 
                          if i.ticker == ticker_upper and i.class_code == self.default_class), None)
            
            # 2. Мягкий поиск (первый попавшийся)
            return match if match else found.instruments[0]
            
        except Exception as e:
            logger.error(f"Ошибка поиска инструмента {ticker}: {e}")
            return None

    @staticmethod
    def _cast_money(money_value) -> float:
        """Конвертирует структуру MoneyValue или Quotation в float.

        Args:
            money_value: Объект цены из API Tinkoff.

        Returns:
            float: Числовое представление цены.
        """
        return money_value.units + money_value.nano / 1e9

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """
        Скачивает исторические свечи за указанный период с нормализацией объемов.

        Автоматически учитывает размер лота инструмента (конвертирует лоты в штуки).

        Args:
            instrument (str): Тикер инструмента.
            interval (str): Интервал свечей (например, '1min', '1hour').
            days (int): Количество дней для загрузки истории.

        Returns:
            pd.DataFrame: DataFrame с колонками OHLCV, где volume в единицах актива.
        """
        interval_name = config.EXCHANGE_INTERVAL_MAPS[ExchangeType.TINKOFF].get(interval)
        if not interval_name:
            logging.error(f"Интервал '{interval}' не поддерживается Тинькофф.")
            return pd.DataFrame()

        api_interval = getattr(CandleInterval, interval_name)
        all_candles = []
        start_date = now() - timedelta(days=days)

        try:
            with Client(self.token) as c:
                # Ищем инструмент через общий метод
                instr_obj = self._find_instrument_obj(c, instrument)
                
                if not instr_obj:
                    logging.error(f"Инструмент {instrument} не найден.")
                    return pd.DataFrame()

                figi = instr_obj.figi
                lot_size = instr_obj.lot
                
                logging.info(f"Загрузка {instrument} (FIGI: {figi}, Lot: {lot_size})...")

                with tqdm(total=days, desc=f"Tinkoff {instrument}", unit="d") as pbar:
                    for candle in c.get_all_candles(figi=figi, from_=start_date, interval=api_interval):
                        all_candles.append({
                            "time": candle.time,
                            "open": self._cast_money(candle.open),
                            "high": self._cast_money(candle.high),
                            "low": self._cast_money(candle.low),
                            "close": self._cast_money(candle.close),
                            
                            # !!! ВАЖНО: Умножаем на лотность !!!
                            "volume": candle.volume * lot_size
                        })

                        if len(all_candles) % 100 == 0:
                            pbar.update(0)
                    pbar.update(days - pbar.n)

        except Exception as e:
            logging.error(f"Ошибка загрузки данных: {e}", exc_info=True)
            return pd.DataFrame()

        return self._process_candles_to_df(all_candles)

    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        """Получает спецификацию инструмента (размер лота, шаг цены).

        Args:
            instrument (str): Тикер инструмента.

        Returns:
            Dict[str, Any]: Словарь с ключами 'lot_size', 'min_price_increment', 'qty_step'.
        """
        try:
            with Client(self.token) as c:
                # Используем тот же метод поиска!
                instr_obj = self._find_instrument_obj(c, instrument)
                
                if not instr_obj:
                    return {}

                return {
                    "lot_size": instr_obj.lot,
                    "min_price_increment": float(quotation_to_decimal(instr_obj.min_price_increment)),
                    "qty_step": float(instr_obj.lot)
                }
        except Exception as e:
            logging.error(f"Ошибка получения инфо: {e}")
            return {}

    def _calculate_single_turnover(self, share: Any, start: Any, end: Any) -> Optional[Dict[str, Any]]:
        """Рассчитывает оборот по одной акции за указанный период (воркер для потока).

        Args:
            share (Any): Объект инструмента из SDK.
            start (datetime): Начало периода.
            end (datetime): Конец периода.

        Returns:
            Optional[Dict[str, Any]]: Словарь {'ticker', 'turnover'} или None, если данных нет.
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
        """Возвращает список самых ликвидных акций (TQBR) по обороту за последние 2 дня.

        Использует многопоточность для ускорения опроса API.

        Args:
            count (int): Количество инструментов в топе.

        Returns:
            List[str]: Список тикеров, отсортированных по убыванию оборота.
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