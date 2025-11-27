import asyncio
import logging
import threading
import pandas as pd
from typing import Optional, List, Dict

from app.core.interfaces import IDataFeed, BaseDataClient
from app.core.calculations.indicators import FeatureEngine
from app.infrastructure.feeds.bybit_stream import BybitStreamDataHandler
from app.infrastructure.feeds.tinkoff_stream import TinkoffStreamDataHandler
from app.infrastructure.feeds.stream_base import BaseStreamDataHandler
from app.shared.primitives import ExchangeType

logger = logging.getLogger(__name__)


class UnifiedDataFeed(IDataFeed):
    """
    Унифицированный фид данных для Live-режима.

    Исправления v2:
    1. Thread-Safety: Добавлен threading.RLock для защиты данных между asyncio-лупом и потоком стратегии.
    2. Performance: Данные хранятся сразу в DataFrame, убрана лишняя конвертация в dict.
    """

    def __init__(self,
                 client: BaseDataClient,
                 exchange: str,
                 instrument: str,
                 interval: str,
                 feature_engine: FeatureEngine,
                 required_indicators: List[Dict],
                 max_buffer_size: int = 1000):

        self.client = client
        self.exchange = exchange
        self.instrument = instrument
        self._interval = interval

        self.feature_engine = feature_engine
        self.required_indicators = required_indicators
        self.max_buffer_size = max_buffer_size

        # RLock позволяет одному и тому же потоку захватывать лок несколько раз (рекурсивно),
        # что удобно, если один метод вызывает другой внутри класса.
        self._lock = threading.RLock()

        # Храним данные сразу в DataFrame, чтобы не конвертировать list<->df на каждом тике.
        self._df: pd.DataFrame = pd.DataFrame()

        self.stream_handler: Optional[BaseStreamDataHandler] = None

    async def warm_up(self, days: int = 3, category: str = "linear"):
        """
        Загрузка истории для инициализации индикаторов.
        Выполняется 1 раз при старте.
        """
        logger.info(f"DataFeed: Загрузка истории за {days} дней для {self.instrument}...")

        loop = asyncio.get_running_loop()

        # Запрос к API выполняется в executor'е, чтобы не блокировать loop
        history_df = await loop.run_in_executor(
            None,
            lambda: self.client.get_historical_data(self.instrument, self._interval, days, category=category)
        )

        with self._lock:
            if history_df.empty:
                logger.warning("DataFeed: История пуста! Индикаторы будут считаться с нуля.")
                self._df = pd.DataFrame()
                return

            # Оставляем только нужный хвост
            self._df = history_df.tail(self.max_buffer_size).copy()
            self._ensure_numeric_types()

            # Первичный расчет индикаторов
            self._recalc_indicators()

            logger.info(f"DataFeed: Разогрев завершен. В памяти {len(self._df)} свечей.")

    def start_stream(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, **kwargs):
        """Инициализирует подключение к вебсокету."""
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
        Принимает новую свечу из стрима, обновляет DataFrame и пересчитывает индикаторы.
        Возвращает True, если свеча была добавлена (новая).
        """
        # Превращаем Series в DataFrame с одной строкой и правильными типами
        new_candle_df = candle_data.to_frame().T
        new_candle_df = new_candle_df.infer_objects()

        with self._lock:
            # Проверка на дубликаты (по времени)
            if not self._df.empty:
                last_time = self._df.iloc[-1]['time']
                new_time = new_candle_df.iloc[0]['time']

                if new_time <= last_time:
                    return False

            # Добавляем новую свечу через concat (это быстрее, чем append в список и пересоздание DF)
            self._df = pd.concat([self._df, new_candle_df], ignore_index=True)

            # Контроль размера буфера
            if len(self._df) > self.max_buffer_size:
                # Отрезаем лишнее сверху. iloc эффективен.
                self._df = self._df.iloc[-self.max_buffer_size:].copy()

            # Пересчет индикаторов
            self._recalc_indicators()

            return True

    def _ensure_numeric_types(self):
        """Принудительное приведение типов для корректной работы индикаторов."""
        cols_to_float = ['open', 'high', 'low', 'close', 'volume']
        for col in cols_to_float:
            if col in self._df.columns:
                self._df[col] = self._df[col].astype(float)

    def _recalc_indicators(self):
        """
        Расчет индикаторов на текущем DataFrame.
        Вызывается внутри блока `with self._lock`.
        """
        if len(self._df) < 2:
            return

        # FeatureEngine модифицирует DF in-place (добавляет колонки)
        # Мы работаем с self._df напрямую.
        self.feature_engine.add_required_features(self._df, self.required_indicators)

    # --- Реализация интерфейса IDataFeed ---

    def get_history(self, length: int = 0) -> pd.DataFrame:
        """
        Возвращает копию исторических данных.
        Критически важно использовать .copy(), чтобы стратегия в другом потоке
        не сломалась, если self._df изменится во время чтения.
        """
        with self._lock:
            if self._df.empty:
                return pd.DataFrame()

            if length > 0:
                return self._df.tail(length).copy()

            return self._df.copy()

    def get_current_candle(self) -> pd.Series:
        """Возвращает последнюю свечу."""
        with self._lock:
            if self._df.empty:
                return pd.Series()
            return self._df.iloc[-1].copy()

    @property
    def interval(self) -> str:
        return self._interval