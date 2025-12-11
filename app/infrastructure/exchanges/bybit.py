"""
Клиент для взаимодействия с API Bybit (Unified Trading).

Этот модуль реализует адаптер для работы с криптобиржей Bybit через HTTP API V5.
Поддерживает спотовую и фьючерсную торговлю (Linear) через Единый Торговый Аккаунт (UTA).

Особенности реализации:
1.  **Reverse Pagination:** Реализован алгоритм загрузки истории "из будущего в прошлое",
    так как API Bybit оптимизировано для выдачи последних данных.
2.  **Safety Guards:** Встроена защита от зацикливания пагинации и дублирования данных.
3.  **Market Scanning:** Эффективная фильтрация тикеров для поиска лидеров ликвидности.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

import pandas as pd
from tqdm import tqdm
from pybit.unified_trading import HTTP

from app.infrastructure.exchanges.base import ExchangeExchangeHandler
from app.shared.types import ExchangeType
from app.shared.config import config

logger = logging.getLogger(__name__)


class BybitHandler(ExchangeExchangeHandler):
    """
    Адаптер для биржи Bybit.

    Реализует интерфейс `ExchangeDataGetter` для получения исторических данных
    и метаинформации об инструментах.

    Attributes:
        client (HTTP): Экземпляр синхронного клиента pybit.
        default_category (str): Категория рынка по умолчанию (обычно 'linear').
    """

    def __init__(self):
        """
        Инициализирует клиента Bybit с настройками из конфигурации.
        """
        super().__init__()

        # Загрузка дефолтной категории (linear/spot) из конфига
        bybit_conf = config.EXCHANGE_SPECIFIC_CONFIG.get(ExchangeType.BYBIT, {})
        self.default_category = bybit_conf.get("DEFAULT_CATEGORY", "linear")

        # Инициализация клиента (testnet=False для реальных данных)
        self.client = HTTP(testnet=False, timeout=10)

        logging.info(f"Bybit Client инициализирован (Category: {self.default_category}).")

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """
        Скачивает исторические свечи (K-Lines) используя обратную пагинацию.

        Алгоритм запрашивает данные, начиная с текущего момента и двигаясь назад
        в прошлое, пока не покроет запрошенный диапазон `days` или пока данные не кончатся.

        Args:
            instrument (str): Тикер инструмента (например, 'BTCUSDT').
            interval (str): Интервал свечей в формате приложения (например, '1hour').
            days (int): Глубина истории в днях.
            **kwargs:
                category (str): Переопределение категории рынка ('linear', 'spot').
                limit (int): Количество свечей в одном запросе (max 1000, default 200).

        Returns:
            pd.DataFrame: DataFrame с колонками ['time', 'open', 'high', 'low', 'close', 'volume'].
        """
        instrument_upper = instrument.upper()
        category = kwargs.get("category", self.default_category)
        limit = kwargs.get("limit", 200)

        # Маппинг интервалов (App format -> Bybit API format)
        api_interval = config.EXCHANGE_INTERVAL_MAPS[ExchangeType.BYBIT].get(interval)
        if not api_interval:
            logging.error(f"Неподдерживаемый интервал для Bybit: {interval}.")
            return pd.DataFrame()

        logging.info(f"Bybit Client: Загрузка {instrument} ({category}), интервал: {interval}...")

        all_candles = []

        # Вычисление временных границ (в миллисекундах)
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

        # Переменная для защиты от бесконечного цикла (хранит время последней полученной свечи)
        last_processed_ts = None

        with tqdm(total=days, desc=f"Bybit {instrument}", unit="d") as pbar:
            current_end_ts = end_ts

            # Цикл пагинации "назад во времени"
            while current_end_ts > start_ts:
                try:
                    resp = self.client.get_kline(
                        category=category,
                        symbol=instrument_upper,
                        interval=api_interval,
                        limit=limit,
                        end=current_end_ts
                    )

                    if resp['retCode'] != 0:
                        logging.error(f"Ошибка API Bybit для {instrument}: {resp['retMsg']}")
                        break

                    # Bybit возвращает список списков: [time, open, high, low, close, volume, turnover]
                    # Сортировка: от новых к старым (descending by time).
                    candles_list = resp['result']['list']

                    if not candles_list:
                        # Данные закончились
                        break

                    # --- Парсинг данных ---
                    batch_data = []
                    for c in candles_list:
                        batch_data.append({
                            "time": int(c[0]),  # Timestamp ms
                            "open": c[1],
                            "high": c[2],
                            "low": c[3],
                            "close": c[4],
                            "volume": c[5]
                        })

                    all_candles.extend(batch_data)

                    # --- Обновление курсора пагинации ---
                    # Берем время самой старой свечи в батче (последняя в списке)
                    oldest_candle_ts = int(candles_list[-1][0])

                    # Защита от зацикливания: если API вернул те же данные, прерываем
                    if last_processed_ts == oldest_candle_ts:
                        logging.warning("Bybit Pagination: Обнаружено зацикливание данных. Остановка.")
                        break
                    last_processed_ts = oldest_candle_ts

                    # Следующий запрос должен быть ДО самой старой полученной свечи (-1 мс)
                    current_end_ts = oldest_candle_ts - 1

                    # --- Визуализация прогресса ---
                    # Рассчитываем, сколько дней мы уже прошли
                    days_processed = (datetime.fromtimestamp(end_ts / 1000) -
                                      datetime.fromtimestamp(current_end_ts / 1000)).days

                    # Обновляем бар только на дельту
                    delta = days_processed - pbar.n
                    if delta > 0:
                        pbar.update(delta)

                    # Если API вернул меньше свечей, чем лимит, значит история кончилась
                    if len(candles_list) < limit:
                        break

                    # Rate Limiting: небольшая пауза для вежливости
                    time.sleep(kwargs.get("sleep", 0.1))

                except Exception as e:
                    logging.error(f"Непредвиденная ошибка при запросе к Bybit: {e}", exc_info=True)
                    break

            # Завершаем прогресс-бар визуально
            if pbar.n < pbar.total:
                pbar.update(pbar.total - pbar.n)

        return self._process_candles_to_df(all_candles)

    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        """
        Получает спецификацию инструмента (размер лота, шаг цены).

        Используется для настройки округления ордеров в RiskManager.

        Args:
            instrument (str): Тикер.
            **kwargs: category (str) - категория рынка.

        Returns:
            Dict[str, Any]: Словарь с ключами:
                - min_order_qty (float): Минимальный объем ордера.
                - qty_step (float): Шаг изменения объема.
        """
        category = kwargs.get("category", self.default_category)
        instrument_upper = instrument.upper()

        try:
            response = self.client.get_instruments_info(category=category, symbol=instrument_upper)

            if response.get("retCode") == 0 and response["result"]["list"]:
                instr_info = response["result"]["list"][0]
                lot_size_filter = instr_info.get("lotSizeFilter", {})

                return {
                    "min_order_qty": float(lot_size_filter.get("minOrderQty", 0)),
                    "qty_step": float(lot_size_filter.get("qtyStep", 0))
                }

            logging.warning(f"Bybit Info: Не найдены данные для {instrument}")
            return {}

        except Exception as e:
            logging.error(f"Bybit Info Error: {e}")
            return {}

    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        """
        Возвращает список самых ликвидных инструментов (USDT-margined).

        Скачивает снепшот всех тикеров за 24 часа и сортирует их по обороту.
        Исключает USDC пары и инверсные контракты.

        Args:
            count (int): Количество инструментов в топе.

        Returns:
            List[str]: Список тикеров, отсортированных по убыванию оборота.
        """
        try:
            # Получаем снепшот рынка (один легкий запрос)
            tickers_response = self.client.get_tickers(category=self.default_category)

            if tickers_response.get("retCode") != 0:
                logging.error(f"Ошибка получения тикеров Bybit: {tickers_response}")
                return []

            all_tickers = tickers_response.get("result", {}).get("list", [])
            liquid_pairs = []

            for ticker in all_tickers:
                symbol = ticker.get('symbol', '')

                # Фильтрация: Оставляем только USDT-контракты
                # Исключаем USDC, опционы и прочее
                if not symbol.endswith('USDT') or 'USDC' in symbol:
                    continue

                try:
                    # turnover24h - оборот в валюте котировки (USDT)
                    turnover = float(ticker.get('turnover24h', 0))
                    liquid_pairs.append({"symbol": symbol, "turnover": turnover})
                except (ValueError, TypeError):
                    continue

            # Сортировка по убыванию оборота
            sorted_pairs = sorted(liquid_pairs, key=lambda x: x['turnover'], reverse=True)

            return [item['symbol'] for item in sorted_pairs[:count]]

        except Exception as e:
            logging.error(f"Bybit Top Liquid Error: {e}")
            return []