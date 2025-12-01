"""
Унифицированный фид данных (Unified Data Feed).

Этот модуль предоставляет реализацию интерфейса `IDataFeed` для работы в режиме
реального времени (Live Trading). Он объединяет загрузку исторических данных (REST)
и получение обновлений через WebSocket.

Ключевые особенности:
1. **Thread-Safety:** Использует `threading.RLock` для безопасного доступа к данным
   из разных потоков (asyncio loop пишет, thread pool стратегии читает).
2. **Performance:** Данные хранятся в `pandas.DataFrame` для быстрого расчета индикаторов.
3. **Indicator Engine:** Автоматически пересчитывает технические индикаторы при
   поступлении каждой новой свечи.
"""

import asyncio
import logging
import threading
from typing import Optional, List, Dict

import pandas as pd

from app.core.interfaces import IDataFeed, BaseDataClient
from app.core.calculations.indicators import FeatureEngine
from app.infrastructure.feeds.bybit_stream import BybitStreamDataHandler
from app.infrastructure.feeds.tinkoff_stream import TinkoffStreamDataHandler
from app.infrastructure.feeds.stream_base import BaseStreamDataHandler
from app.shared.primitives import ExchangeType

logger = logging.getLogger(__name__)


class UnifiedDataFeed(IDataFeed):
    """
    Основной поставщик данных для Live-стратегий.

    Управляет буфером свечей (DataFrame), поддерживает его актуальность
    и предоставляет потокобезопасный доступ к истории для стратегий.

    Attributes:
        client (BaseDataClient): Клиент для загрузки исторической части данных.
        exchange (str): Название биржи.
        instrument (str): Тикер инструмента.
        max_buffer_size (int): Максимальное количество хранящихся свечей.
    """

    def __init__(self,
                 client: BaseDataClient,
                 exchange: str,
                 instrument: str,
                 interval: str,
                 feature_engine: FeatureEngine,
                 required_indicators: List[Dict],
                 max_buffer_size: int = 1000):
        """
        Инициализирует фид.

        Args:
            client (BaseDataClient): API клиент биржи.
            exchange (str): Имя биржи.
            instrument (str): Тикер.
            interval (str): Интервал свечей.
            feature_engine (FeatureEngine): Сервис расчета индикаторов.
            required_indicators (List[Dict]): Список индикаторов, требуемых стратегией.
            max_buffer_size (int): Лимит размера DataFrame в памяти.
        """
        self.client = client
        self.exchange = exchange
        self.instrument = instrument
        self._interval = interval

        self.feature_engine = feature_engine
        self.required_indicators = required_indicators
        self.max_buffer_size = max_buffer_size

        # RLock (Reentrant Lock) позволяет потоку-владельцу захватывать блокировку
        # повторно, что удобно, если один защищенный метод вызывает другой.
        # Защищает self._df от одновременной записи (WS) и чтения (Strategy).
        self._lock = threading.RLock()

        # Основное хранилище данных. Инициализируется пустым.
        self._df: pd.DataFrame = pd.DataFrame()

        self.stream_handler: Optional[BaseStreamDataHandler] = None

    async def warm_up(self, days: int = 3, category: str = "linear"):
        """
        Первичная загрузка истории ("разогрев").

        Выполняется перед запуском стратегии, чтобы у индикаторов (SMA, EMA)
        было достаточно данных для корректного расчета.

        Args:
            days (int): За сколько дней загружать историю.
            category (str): Категория рынка (для Bybit).
        """
        logger.info(f"DataFeed: Загрузка истории за {days} дней для {self.instrument}...")

        loop = asyncio.get_running_loop()

        # Запрос к API выполняется в executor'е (отдельном потоке),
        # чтобы блокирующий HTTP-запрос не заморозил asyncio loop.
        history_df = await loop.run_in_executor(
            None,
            lambda: self.client.get_historical_data(self.instrument, self._interval, days, category=category)
        )

        with self._lock:
            if history_df.empty:
                logger.warning("DataFeed: История пуста! Индикаторы будут считаться с нуля.")
                self._df = pd.DataFrame()
                return

            # Оставляем только последние N свечей согласно лимиту буфера
            self._df = history_df.tail(self.max_buffer_size).copy()
            self._ensure_numeric_types()

            # Рассчитываем индикаторы на исторических данных
            self._recalc_indicators()

            logger.info(f"DataFeed: Разогрев завершен. В памяти {len(self._df)} свечей.")

    def start_stream(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, **kwargs):
        """
        Фабричный метод для запуска соответствующего WebSocket/gRPC стрима.

        Args:
            event_queue (asyncio.Queue): Очередь для отправки событий.
            loop (asyncio.AbstractEventLoop): Главный цикл событий.
            **kwargs: Дополнительные параметры (например, category).

        Returns:
            Coroutine: Задача стриминга данных.

        Raises:
            ValueError: Если биржа не поддерживается.
        """
        if self.exchange == ExchangeType.TINKOFF:
            self.stream_handler = TinkoffStreamDataHandler(event_queue, self.instrument, self._interval)
        elif self.exchange == ExchangeType.BYBIT:
            self.stream_handler = BybitStreamDataHandler(
                event_queue, self.instrument, self._interval, loop,
                channel_type=kwargs.get('category', 'linear'), testnet=False
            )
        else:
            raise ValueError(f"Unknown exchange: {self.exchange}")

        return self.stream_handler.stream_data()

    async def process_candle(self, candle_data: pd.Series) -> bool:
        """
        Обрабатывает новую свечу, пришедшую из стрима.

        Алгоритм:
        1. Проверяет время свечи (защита от дублей и старых данных).
        2. Добавляет свечу в DataFrame.
        3. Обрезает DataFrame, если превышен размер буфера.
        4. Пересчитывает индикаторы для новой свечи.

        Args:
            candle_data (pd.Series): Данные новой свечи.

        Returns:
            bool: True, если свеча была успешно добавлена и обработана.
        """
        # Превращаем Series в DataFrame с одной строкой
        new_candle_df = candle_data.to_frame().T
        new_candle_df = new_candle_df.infer_objects()

        with self._lock:
            # Проверка на дубликаты (по времени)
            if not self._df.empty:
                last_time = self._df.iloc[-1]['time']
                new_time = new_candle_df.iloc[0]['time']

                # Игнорируем, если время меньше или равно последнему
                if new_time <= last_time:
                    return False

            # Добавляем новую свечу
            # pd.concat эффективнее append для DataFrame
            self._df = pd.concat([self._df, new_candle_df], ignore_index=True)

            # Контроль размера буфера (FIFO)
            if len(self._df) > self.max_buffer_size:
                self._df = self._df.iloc[-self.max_buffer_size:].copy()

            # Пересчет индикаторов
            self._recalc_indicators()

            return True

    def _ensure_numeric_types(self):
        """
        Принудительно приводит колонки цен к float.
        Необходимо, так как иногда данные могут приходить как object/string.
        """
        cols_to_float = ['open', 'high', 'low', 'close', 'volume']
        for col in cols_to_float:
            if col in self._df.columns:
                self._df[col] = self._df[col].astype(float)

    def _recalc_indicators(self):
        """
        Вызывает FeatureEngine для расчета индикаторов.
        Метод выполняется внутри блокировки self._lock.
        """
        if len(self._df) < 2:
            return

        # FeatureEngine модифицирует DF in-place (добавляет колонки)
        # Мы работаем с self._df напрямую.
        self.feature_engine.add_required_features(self._df, self.required_indicators)

    # --- Реализация интерфейса IDataFeed ---

    def get_history(self, length: int = 0) -> pd.DataFrame:
        """
        Возвращает исторические данные для стратегии.

        ВНИМАНИЕ: Возвращает глубокую копию (.copy()), чтобы стратегия
        не могла случайно изменить данные в буфере фида и чтобы избежать
        ошибок конкурентного доступа при чтении.

        Args:
            length (int): Сколько последних свечей вернуть. 0 = все.

        Returns:
            pd.DataFrame: Копия данных.
        """
        with self._lock:
            if self._df.empty:
                return pd.DataFrame()

            if length > 0:
                return self._df.tail(length).copy()

            return self._df.copy()

    def get_current_candle(self) -> pd.Series:
        """
        Возвращает последнюю (текущую закрытую) свечу.

        Returns:
            pd.Series: Последняя строка данных.
        """
        with self._lock:
            if self._df.empty:
                return pd.Series()
            return self._df.iloc[-1].copy()

    @property
    def interval(self) -> str:
        """Таймфрейм потока данных."""
        return self._interval