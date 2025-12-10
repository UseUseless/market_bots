"""
Базовые классы для потоковых данных (Streaming Feeds).

Этот модуль определяет абстрактный интерфейс для всех поставщиков
рыночных данных в реальном времени (Live Data). Любая реализация
подключения к WebSocket или gRPC стриму биржи должна наследоваться
от этого класса.
"""

from abc import ABC, abstractmethod
from asyncio import Queue as AsyncQueue


class BaseStreamDataHandler(ABC):
    """
    Абстрактный базовый класс обработчика потоковых данных.

    Определяет единый интерфейс инициализации и запуска стрима.
    Используется классом `LiveDataProvider` для унифицированного запуска
    разных источников данных (Bybit, Tinkoff и т.д.).

    Attributes:
        events_queue (AsyncQueue): Очередь, в которую будут помещаться события `MarketEvent`.
        instrument (str): Тикер инструмента (например, 'BTCUSDT').
        interval_str (str): Интервал свечей (например, '1min').
    """

    def __init__(self, events_queue: AsyncQueue, instrument: str, interval_str: str):
        """
        Инициализирует базовые параметры стрима.

        Args:
            events_queue (AsyncQueue): Асинхронная очередь для отправки данных в ядро.
            instrument (str): Идентификатор инструмента.
            interval_str (str): Временной интервал.
        """
        self.events_queue = events_queue
        self.instrument = instrument
        self.interval_str = interval_str

    @abstractmethod
    async def stream_data(self):
        """
        Запускает бесконечный цикл прослушивания данных от биржи.

        Этот метод должен быть реализован в подклассах. Он отвечает за:
        1. Установку соединения (WebSocket/gRPC).
        2. Подписку на топик (свечи/сделки).
        3. Преобразование входящих сообщений в `MarketEvent`.
        4. Отправку событий в `self.events_queue`.
        5. Обработку ошибок соединения и реконнект.

        Raises:
            NotImplementedError: Если метод не переопределен в подклассе.
        """
        raise NotImplementedError("Метод stream_data должен быть реализован в подклассе.")