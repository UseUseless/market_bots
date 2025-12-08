"""
Клиент для взаимодействия с API Bybit (Unified Trading).

Этот модуль реализует адаптер для работы с криптобиржей Bybit.
Поддерживает спотовую и фьючерсную торговлю через Unified Trading Account (UTA).
Использует библиотеку `pybit` для HTTP-запросов.

Особенности реализации:
1.  **Unified Trading**: Работает с категорией 'linear' (USDT Perpetual) по умолчанию.
2.  **Pagination**: Реализует обратную пагинацию по времени для загрузки глубокой истории.
3.  **Market Scanning**: Эффективно сканирует рынок одним запросом для поиска лидеров оборота.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any

import pandas as pd
from tqdm import tqdm
from pybit.unified_trading import HTTP

from app.infrastructure.exchanges.base import BaseExchangeHandler
from app.shared.primitives import ExchangeType
from app.shared.config import config

logger = logging.getLogger(__name__)


class BybitHandler(BaseExchangeHandler):
    """
    Адаптер для биржи Bybit.
    Работает в режиме Read-Only.
    """

    def __init__(self):
        """
        Инициализирует клиента Bybit.
        """
        super().__init__()

        self.default_category = config.EXCHANGE_SPECIFIC_CONFIG[ExchangeType.BYBIT]["DEFAULT_CATEGORY"]

        self.client = HTTP(testnet=False, timeout=10)

        logging.info(f"Bybit Client инициализирован для получения данных.")

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """
        Скачивает исторические свечи (K-Lines).

        Реализует сложную логику пагинации, так как Bybit отдает свечи от новых к старым
        (обратный хронологический порядок) и имеет лимит на кол-во свечей в запросе.

        Args:
            instrument (str): Тикер (например, BTCUSDT).
            interval (str): Интервал (например, '60' для 1 часа).
            days (int): Глубина истории.
            **kwargs:
                category (str): 'linear' (фьючерсы), 'spot' и т.д. Default: 'linear'.
                limit (int): Лимит свечей на запрос. Default: 200.

        Returns:
            pd.DataFrame: DataFrame с историей.
        """
        instrument_upper = instrument.upper()
        category = kwargs.get("category", self.default_category)
        # Лимит 200 безопасен и стабилен, хотя API позволяет до 1000
        limit = kwargs.get("limit", 200)

        logging.info(f"Bybit Client: Загрузка {instrument} ({category}), интервал: {interval}")

        # Получаем код интервала для API из конфига
        api_interval = config.EXCHANGE_INTERVAL_MAPS[ExchangeType.BYBIT].get(interval)
        if not api_interval:
            logging.error(f"Неподдерживаемый интервал для Bybit: {interval}.")
            return pd.DataFrame()

        all_candles = []

        # Расчет временных границ в миллисекундах
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

        # Используем общий цикл с TQDM для визуализации прогресса
        with tqdm(total=days, desc=f"Bybit {instrument}", unit="d") as pbar:
            current_end_ts = end_ts

            # Цикл пагинации "назад во времени"
            while start_ts < current_end_ts:
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

                    candles_list = resp['result']['list']
                    if not candles_list:
                        # Данные кончились раньше, чем мы ожидали
                        break

                    # Преобразуем формат Bybit [time, open, high, low, close, volume, turnover]
                    # в список словарей для передачи в базовый метод.
                    # ВАЖНО: Bybit отдает timestamp как строку в ms.
                    for c in candles_list:
                        all_candles.append({
                            "time": int(c[0]),
                            "open": c[1],
                            "high": c[2],
                            "low": c[3],
                            "close": c[4],
                            "volume": c[5]
                        })

                    # Обновляем курсор времени: берем время самой старой полученной свечи (последней в списке)
                    oldest_candle_time = int(candles_list[-1][0])

                    # Если API вернул данные, где самая старая свеча новее или равна нашему запросу (странность API),
                    # прерываем, чтобы не уйти в вечный цикл.
                    if oldest_candle_time >= current_end_ts:
                        break

                    # Сдвигаем курсор НАЗАД на 1 мс от самой старой свечи, чтобы не получить её дубль
                    current_end_ts = oldest_candle_time - 1

                    # Обновление прогресс-бара
                    days_loaded = (datetime.fromtimestamp(end_ts / 1000) - datetime.fromtimestamp(current_end_ts / 1000)).days
                    if days_loaded > pbar.n:
                        pbar.update(days_loaded - pbar.n)

                    # Если вернули меньше лимита, значит история исчерпана
                    if len(candles_list) < limit:
                        break

                    # Небольшая пауза для вежливости к API
                    time.sleep(kwargs.get("sleep", 0.1))

                except Exception as e:
                    logging.error(f"Непредвиденная ошибка при запросе к Bybit: {e}")
                    break

            # Завершаем бар визуально
            if pbar.n < pbar.total:
                pbar.update(pbar.total - pbar.n)

        # Делегируем создание DataFrame базовому классу
        return self._process_candles_to_df(all_candles)

    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        """
        Получает спецификацию инструмента (шаг цены, лотность).

        Args:
            instrument (str): Тикер.
            **kwargs: category ('linear'/'spot').

        Returns:
            Dict[str, Any]: Словарь с 'min_order_qty' и 'qty_step'.
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
            return {}
        except Exception as e:
            logging.error(f"Bybit Info Error: {e}")
            return {}

    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        """
        Возвращает топ ликвидных инструментов (USDT-margined).

        Использует один эффективный запрос `get_tickers`, который возвращает
        снэпшот рынка за 24 часа. Фильтрует инструменты, оставляя только
        USDT-контракты (исключая USDC и инверсные).

        Args:
            count (int): Кол-во инструментов.

        Returns:
            List[str]: Список тикеров, отсортированных по обороту.
        """
        try:
            tickers_response = self.client.get_tickers(category=self.default_category)
            if tickers_response.get("retCode") != 0:
                return []

            all_tickers = tickers_response.get("result", {}).get("list", [])
            liquid_pairs = []

            for ticker in all_tickers:
                symbol = ticker.get('symbol', '')

                # Фильтр: только USDT контракты (исключаем USDC и опционы)
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