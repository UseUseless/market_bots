from abc import ABC, abstractmethod
from asyncio import Queue as AsyncQueue


class BaseStreamDataHandler(ABC):
    """Абстрактный 'контракт' для всех поставщиков live-данных."""

    def __init__(self, events_queue: AsyncQueue, instrument: str, interval_str: str):
        self.events_queue = events_queue
        self.instrument = instrument
        self.interval_str = interval_str

    @abstractmethod
    async def stream_data(self):
        """Основная асинхронная задача, которая слушает данные и кладет их в очередь."""
        raise NotImplementedError
