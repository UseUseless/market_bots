"""
Базовый класс для адаптеров бирж (Base Exchange Handler).

Этот модуль определяет родительский класс `BaseExchangeHandler`, который реализует
общую логику для всех коннекторов к биржам (Bybit, Tinkoff и др.).

Основные задачи модуля:
1.  **Нормализация данных:** Приведение разнородных ответов API (свечей) к единому
    формату Pandas DataFrame с правильными типами данных и таймзоной UTC.
2.  **Безопасное исполнение:** Обертка над отправкой ордеров с централизованным
    логированием и перехватом критических ошибок (Safety Net).
3.  **Соблюдение контрактов:** Реализация интерфейсов `BaseDataClient` и `BaseTradeClient`.
"""

import logging
from abc import abstractmethod
from typing import Optional, List, Dict, Any

import pandas as pd

from app.core.interfaces import BaseDataClient, BaseTradeClient, TradeModeType

logger = logging.getLogger(__name__)


class BaseExchangeHandler(BaseDataClient, BaseTradeClient):
    """
    Абстрактный базовый класс адаптера биржи.

    Предоставляет реализацию общих утилитных методов, оставляя специфику
    взаимодействия с конкретным API наследникам.

    Attributes:
        trade_mode (str): Режим работы клиента ('SANDBOX' или 'REAL').
    """

    def __init__(self, trade_mode: TradeModeType):
        """
        Инициализирует базовый обработчик.

        Args:
            trade_mode (TradeModeType): Режим торговли. Влияет на выбор API-endpoint'ов
                (тестовая сеть или боевая) в классах-наследниках.
        """
        self.trade_mode = trade_mode.upper()

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

    def place_market_order(self, instrument_id: str, quantity: float, direction: str, **kwargs) -> Optional[Any]:
        """
        Шаблонный метод (Template Method) отправки рыночного ордера.

        Обеспечивает единый стандарт логирования и обработки ошибок для всех бирж.
        Фактическая отправка запроса делегируется методу `_place_order_impl`.

        Args:
            instrument_id (str): Идентификатор инструмента (Ticker для Bybit, FIGI для Tinkoff).
            quantity (float): Объем ордера (в лотах или монетах).
            direction (str): Направление сделки ('BUY' или 'SELL').
            **kwargs: Дополнительные параметры (category, account_id и т.д.),
                      специфичные для конкретной биржи.

        Returns:
            Optional[Any]: Объект ответа API (JSON dict или Pydantic model),
                           если ордер успешен. Возвращает None в случае ошибки.
        """
        logging.info(f"[{self.__class__.__name__}] Отправка ордера: {direction} {quantity} {instrument_id}...")

        try:
            # Делегирование исполнения абстрактному методу (реализуется наследником)
            result = self._place_order_impl(instrument_id, quantity, direction, **kwargs)

            if result:
                logging.info(f"[{self.__class__.__name__}] Ордер успешно размещен. Ответ: {result}")
                return result
            else:
                # Если наследник вернул None, он уже должен был залогировать причину
                logging.error(f"[{self.__class__.__name__}] API вернул пустой результат/ошибку.")
                return None

        except Exception as e:
            # "Safety Net": Ловим любые непредвиденные ошибки (сеть, парсинг),
            # чтобы не обрушить вызывающий поток (LiveExecutionHandler).
            logging.error(f"[{self.__class__.__name__}] Критическая ошибка размещения ордера: {e}", exc_info=True)
            return None

    @abstractmethod
    def _place_order_impl(self, instrument_id: str, quantity: float, direction: str, **kwargs) -> Optional[Any]:
        """
        Внутренняя реализация отправки ордера (Hook Method).

        Должна быть переопределена в классе-наследнике для вызова
        специфичного API конкретной биржи.

        Args:
            instrument_id (str): Идентификатор инструмента.
            quantity (float): Объем.
            direction (str): Направление.
            **kwargs: Доп. параметры.

        Returns:
            Optional[Any]: Ответ API или None.
        """
        raise NotImplementedError("Метод _place_order_impl должен быть реализован в подклассе.")