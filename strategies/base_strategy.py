from queue import Queue
import pandas as pd
from abc import ABC, abstractmethod

from core.event import MarketEvent

class BaseStrategy(ABC):
    """
    Абстрактный базовый класс для всех торговых стратегий.
    Определяет "контракт", которому должна следовать каждая стратегия:
    - Предоставлять информацию о себе (figi, interval, sl/tp).
    - Уметь подготавливать данные (рассчитывать индикаторы).
    - Уметь генерировать сигналы на основе рыночных данных.
    """

    candle_interval: str

    def __init__(self, events_queue: Queue, figi: str):
        self.events_queue = events_queue
        self.figi: str = figi

        self.name: str = self.__class__.__name__

    # --- КОНТРАКТ: Методы, которые должна определить каждая стратегия ---

    @abstractmethod
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Метод для подготовки данных. Стратегия сама добавляет
        в DataFrame необходимые ей индикаторы.
        Должен быть реализован в дочернем классе.
        """
        raise NotImplementedError("Метод prepare_data должен быть реализован в дочернем классе.")

    @abstractmethod
    def calculate_signals(self, event: MarketEvent):
        """
        Основной метод, который анализирует рыночные данные (MarketEvent)
        и генерирует торговые сигналы (SignalEvent).
        Должен быть реализован в дочернем классе.
        """
        raise NotImplementedError("Метод calculate_signals должен быть реализован в дочернем классе.")