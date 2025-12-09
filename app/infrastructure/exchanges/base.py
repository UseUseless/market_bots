"""
Базовый класс для адаптеров бирж (Base Exchange Handler).

Этот модуль определяет родительский класс `ExchangeExchangeHandler`, который реализует
общую логику для всех коннекторов к биржам (Bybit, Tinkoff и др.).

Основные задачи модуля:
1.  **Нормализация данных:** Приведение разнородных ответов API (свечей) к единому
    формату Pandas DataFrame с правильными типами данных и таймзоной UTC.
2.  **Безопасное исполнение:** Обертка над отправкой ордеров с централизованным
    логированием и перехватом критических ошибок (Safety Net).
3.  **Соблюдение контрактов:** Реализация интерфейсов `ExchangeDataGetter` и `BaseTradeClient`.
"""

import logging
from typing import List, Dict, Any

import pandas as pd

from app.shared.interfaces import ExchangeDataGetter

logger = logging.getLogger(__name__)


class ExchangeExchangeHandler(ExchangeDataGetter):
    """
    Абстрактный базовый класс адаптера биржи.
    """

    def _process_candles_to_df(self, candles: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Преобразует список "сырых" словарей свечей в валидированный DataFrame.

        Этот метод стандартизирует данные от разных бирж:
        1.  Приводит временные метки к UTC (timezone-aware).
        2.  Гарантирует числовой формат цен (float), защищая от строк типа "100.0".
        3.  Сортирует данные по времени (критично для индикаторов).

        Args:
            candles (List[Dict]): Список словарей свечей. Ожидается наличие ключей:
                'time', 'open', 'high', 'low', 'close', 'volume'.

        Returns:
            pd.DataFrame: DataFrame с индексом RangeIndex и колонками OHLCV.
                          Возвращает пустой DF, если входной список пуст.
        """
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)

        # --- 1. Обработка времени ---
        # Критически важно для сопоставления данных с разных бирж.
        # Всегда приводим к UTC.
        if pd.api.types.is_numeric_dtype(df['time']):
            # Если time пришло как timestamp (int/float), обычно это миллисекунды
            df['time'] = pd.to_datetime(df['time'], unit='ms', utc=True)
        else:
            # Если time уже строка или datetime, форсируем UTC
            df['time'] = pd.to_datetime(df['time'], utc=True)

        # --- 2. Приведение типов (Safety Casting) ---
        # Биржи иногда присылают цены как строки ("100.50"), что ломает математику.
        # errors='coerce' превратит битые данные в NaN, которые потом можно обработать.
        cols_to_numeric = ["open", "high", "low", "close", "volume"]
        for col in cols_to_numeric:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # --- 3. Финализация структуры ---
        # Гарантируем хронологический порядок (старые -> новые)
        df = df.sort_values('time').reset_index(drop=True)

        # Оставляем только стандартизированные колонки, отсекая лишний шум от API
        available_cols = ["time"] + [c for c in cols_to_numeric if c in df.columns]
        return df[available_cols]