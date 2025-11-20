import asyncio
import logging
import pandas as pd
from typing import Optional, List, Dict
from collections import deque

from app.core.interfaces.abstract_feed import IDataFeed
from app.core.services.feature_engine import FeatureEngine
from app.utils.clients.abc import BaseDataClient
from app.core.data.feeds.stream import BaseStreamDataHandler, TinkoffStreamDataHandler, BybitStreamDataHandler

logger = logging.getLogger(__name__)


class UnifiedDataFeed(IDataFeed):
    """
    Унифицированный фид данных для Live-режима.
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
        # ИСПРАВЛЕНИЕ: Сохраняем в защищенную переменную
        self._interval = interval

        self.feature_engine = feature_engine
        self.required_indicators = required_indicators
        self.max_buffer_size = max_buffer_size

        self._buffer: List[dict] = []
        self._df_cache: Optional[pd.DataFrame] = None
        self._df_dirty = True

        self.stream_handler: Optional[BaseStreamDataHandler] = None
        self._new_candle_event = asyncio.Event()

    async def warm_up(self, days: int = 3, category: str = "linear"):
        """Загрузка истории для инициализации индикаторов."""
        logger.info(f"DataFeed: Загрузка истории за {days} дней для {self.instrument}...")

        loop = asyncio.get_running_loop()
        # Используем self._interval вместо self.interval
        history_df = await loop.run_in_executor(
            None,
            lambda: self.client.get_historical_data(self.instrument, self._interval, days, category=category)
        )

        if history_df.empty:
            logger.warning("DataFeed: История пуста! Индикаторы будут считаться с нуля.")
            return

        self._buffer = history_df.tail(self.max_buffer_size).to_dict('records')
        self._recalc_indicators()
        logger.info(f"DataFeed: Разогрев завершен. Загружено {len(self._buffer)} свечей.")

    def start_stream(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, **kwargs):
        """Инициализирует подключение к вебсокету."""
        if self.exchange == 'tinkoff':
            self.stream_handler = TinkoffStreamDataHandler(event_queue, self.instrument, self._interval)
        elif self.exchange == 'bybit':
            self.stream_handler = BybitStreamDataHandler(
                event_queue, self.instrument, self._interval, loop,
                channel_type=kwargs.get('category', 'linear'), testnet=False
            )
        else:
            raise ValueError(f"Unknown exchange: {self.exchange}")

        return self.stream_handler.stream_data()

    async def process_candle(self, candle_data: pd.Series) -> bool:
        if self._buffer and candle_data['time'] <= self._buffer[-1]['time']:
            return False

        self._buffer.append(candle_data.to_dict())

        if len(self._buffer) > self.max_buffer_size:
            self._buffer.pop(0)

        self._df_dirty = True
        self._recalc_indicators()
        return True

    def _recalc_indicators(self):
        if len(self._buffer) < 50:
            return

        df = pd.DataFrame(self._buffer)

        cols_to_float = ['open', 'high', 'low', 'close', 'volume']
        for col in cols_to_float:
            if col in df.columns:
                df[col] = df[col].astype(float)

        self.feature_engine.add_required_features(df, self.required_indicators)
        self._buffer = df.to_dict('records')
        self._df_dirty = False

    # --- Реализация интерфейса IDataFeed ---

    def get_history(self, length: int = 0) -> pd.DataFrame:
        df = pd.DataFrame(self._buffer)
        if length > 0:
            return df.tail(length)
        return df

    def get_current_candle(self) -> pd.Series:
        if not self._buffer:
            return pd.Series()
        return pd.Series(self._buffer[-1])

    @property
    def interval(self) -> str:
        # ИСПРАВЛЕНИЕ: Возвращаем защищенную переменную
        return self._interval