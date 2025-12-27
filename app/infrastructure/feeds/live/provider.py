"""
Унифицированный фид данных (Unified Data Feed).

Этот модуль предоставляет реализацию интерфейса `MarketDataProvider` для работы в режиме
реального времени (Live Trading). Он объединяет загрузку исторических данных (REST)
и получение обновлений через WebSocket, обеспечивая непрерывность истории.

Оптимизация (v2):
    Этот класс теперь выступает только как "тупой" буфер данных.
    Он НЕ рассчитывает индикаторы. Расчет перенесен на сторону потребителя (Стратегии),
    чтобы избежать Race Conditions и блокировки Event Loop'а тяжелыми вычислениями.
"""

import asyncio
import logging
import threading
from typing import Optional

import pandas as pd

from app.shared.interfaces import MarketDataProvider, ExchangeDataGetter
from app.infrastructure.feeds.live.streams.bybit import BybitStreamDataHandler
from app.infrastructure.feeds.live.streams.tinkoff import TinkoffStreamDataHandler
from app.infrastructure.feeds.live.streams.base import BaseStreamDataHandler
from app.shared.types import ExchangeType

logger = logging.getLogger(__name__)


class LiveDataProvider(MarketDataProvider):
    """
    Провайдер данных реального времени.

    Управляет буфером свечей (DataFrame), поддерживает его актуальность
    через WebSocket и предоставляет потокобезопасный доступ к истории.

    Attributes:
        client (ExchangeDataGetter): Клиент для загрузки исторической части данных.
        exchange (str): Название биржи.
        instrument (str): Тикер инструмента.
        max_buffer_size (int): Лимит размера DataFrame в памяти (скользящее окно).
        _lock (threading.RLock): Блокировка для защиты DataFrame от гонки потоков.
        _df (pd.DataFrame): Внутренний буфер свечей.
        stream_handler (Optional[BaseStreamDataHandler]): Активный обработчик вебсокет-соединения.
    """

    def __init__(self,
                 client: ExchangeDataGetter,
                 exchange: str,
                 instrument: str,
                 interval: str,
                 max_buffer_size: int = 1000):
        """
        Инициализирует провайдер данных.

        Args:
            client (ExchangeDataGetter): API клиент биржи.
            exchange (str): Имя биржи.
            instrument (str): Тикер.
            interval (str): Интервал свечей.
            max_buffer_size (int, optional): Максимальное количество свечей в памяти. Defaults to 1000.
        """
        self.client = client
        self.exchange = exchange
        self.instrument = instrument
        self._interval = interval
        self.max_buffer_size = max_buffer_size

        # RLock обязателен: запись идет из AsyncIO Loop, чтение - из ThreadPool стратегии
        self._lock = threading.RLock()

        self._df: pd.DataFrame = pd.DataFrame()
        self.stream_handler: Optional[BaseStreamDataHandler] = None

    async def warm_up(self, days: int = 3, category: str = "linear"):
        """
        Загружает исторические данные через REST API ("разогрев").

        Выполняется в Executor'е, чтобы не блокировать Event Loop тяжелым I/O запросом.

        Args:
            days (int, optional): Глубина истории в днях. Defaults to 3.
            category (str, optional): Категория рынка (для Bybit). Defaults to "linear".
        """
        logger.info(f"DataFeed: Загрузка истории за {days} дней для {self.instrument}...")

        loop = asyncio.get_running_loop()

        # Запуск синхронного REST-запроса в отдельном потоке
        history_df = await loop.run_in_executor(
            None,
            lambda: self.client.get_historical_data(self.instrument, self._interval, days, category=category)
        )

        with self._lock:
            if history_df.empty:
                logger.warning("DataFeed: История пуста! Буфер начнется с нуля.")
                self._df = pd.DataFrame()
                return

            # Обрезка истории до размера буфера, если скачали слишком много
            if len(history_df) > self.max_buffer_size:
                self._df = history_df.tail(self.max_buffer_size).reset_index(drop=True)
            else:
                self._df = history_df.copy()

            self._ensure_numeric_types()

            logger.info(f"DataFeed: Разогрев завершен. В памяти {len(self._df)} свечей.")

    def start_stream(self, event_queue: asyncio.Queue, loop: asyncio.AbstractEventLoop, **kwargs):
        """
        Инициализирует и запускает WebSocket/gRPC поток данных.

        Args:
            event_queue (asyncio.Queue): Очередь для отправки событий MarketEvent.
            loop (asyncio.AbstractEventLoop): Ссылка на Event Loop.
            **kwargs: Дополнительные параметры (например, category).

        Returns:
            Coroutine: Асинхронная задача запуска стрима.

        Raises:
            ValueError: Если указана неизвестная биржа.
        """
        if self.exchange == ExchangeType.TINKOFF:
            token = self.client.token
            
            # Запрашиваем размер лота ---
            # Метод get_instrument_info уже есть в TinkoffHandler, используем его.
            # Он возвращает dict: {'lot_size': 10, ...}
            lot_size = 1
            try:
                # Это синхронный вызов HTTP API. 
                # Так как это происходит 1 раз при старте стратегии, блокировка Loop на 0.1с допустима.
                info = self.client.get_instrument_info(self.instrument)
                lot_size = info.get("lot_size", 1)
            except Exception as e:
                logger.error(f"Не удалось получить лотность для {self.instrument}, используем 1: {e}")

            # Передаем lot_size в конструктор
            self.stream_handler = TinkoffStreamDataHandler(
                event_queue, self.instrument, self._interval, token, lot_size=lot_size
            )

        elif self.exchange == ExchangeType.BYBIT:
            self.stream_handler = BybitStreamDataHandler(
                event_queue, self.instrument, self._interval, loop,
                channel_type=kwargs.get('category', 'linear'),
                testnet=False
            )

        else:
            raise ValueError(f"Unknown exchange: {self.exchange}")

        return self.stream_handler.stream_data()

    async def process_candle(self, candle_data: pd.Series) -> bool:
        """
        Обрабатывает новую свечу из стрима.

        Добавляет свечу в буфер, обрезает его при переполнении.
        Больше никаких тяжелых расчетов здесь нет.

        Args:
            candle_data (pd.Series): Данные свечи (OHLCV).

        Returns:
            bool: True, если свеча новая и успешно добавлена; False, если дубликат.
        """
        # Конвертация Series -> DataFrame (одна строка) для concat
        new_candle_df = candle_data.to_frame().T
        new_candle_df = new_candle_df.infer_objects()

        with self._lock:
            # Дедупликация: проверка временной метки
            if not self._df.empty:
                last_time = self._df.iloc[-1]['time']
                new_time = new_candle_df.iloc[0]['time']

                if new_time <= last_time:
                    return False

            # Добавление свечи
            self._df = pd.concat([self._df, new_candle_df], ignore_index=True)

            # Управление памятью: поддержание размера скользящего окна
            if len(self._df) > self.max_buffer_size:
                rows_to_drop = len(self._df) - self.max_buffer_size
                # Эффективная обрезка через slicing
                self._df = self._df.iloc[rows_to_drop:].reset_index(drop=True)

            return True

    def _ensure_numeric_types(self):
        """
        Принудительное приведение цен к типу float.
        Защита от строковых значений, которые могут прийти из API.
        """
        cols_to_float = ['open', 'high', 'low', 'close', 'volume']
        for col in cols_to_float:
            if col in self._df.columns:
                self._df[col] = self._df[col].astype(float)

    # --- Implementation of MarketDataProvider Interface ---

    def get_history(self, length: int = 0) -> pd.DataFrame:
        """
        Предоставляет срез исторических данных.

        ВАЖНО: Возвращает глубокую копию (.copy()), чтобы потребитель (стратегия)
        мог безопасно модифицировать данные (например, добавлять индикаторы),
        не ломая основной буфер провайдера.

        Args:
            length (int, optional): Количество последних свечей. Если 0 — возвращает весь буфер. Defaults to 0.

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
        Возвращает последнюю закрытую свечу из буфера.

        Returns:
            pd.Series: Последняя доступная свеча.
        """
        with self._lock:
            if self._df.empty:
                return pd.Series()
            return self._df.iloc[-1].copy()

    @property
    def interval(self) -> str:
        """Возвращает текущий интервал данных."""
        return self._interval