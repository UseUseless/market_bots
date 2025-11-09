from queue import Queue
import pandas as pd
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from core.event import MarketEvent

class BaseStrategy(ABC):
    """
    Абстрактный базовый класс для всех торговых стратегий.
    Определяет "контракт", которому должна следовать каждая стратегия:
    - Предоставлять информацию о себе (instrument, interval, sl/tp).
    - Уметь подготавливать данные (рассчитывать индикаторы).
    - Уметь генерировать сигналы на основе рыночных данных.
    """

    candle_interval: str
    min_history_needed: int = 1  # Минимальное кол-во свечей по умолчанию
    # Декларация необходимых индикаторов
    # Каждая дочерняя стратегия будет переопределять этот список.
    # Пример: [{"name": "ema", "params": {"period": 9}}, {"name": "sma", "params": {"period": 20, "column": "volume"}}]
    required_indicators: List[Dict[str, Any]] = []
    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None):
        self.events_queue = events_queue
        self.instrument: str = instrument

        self.name: str = self.__class__.__name__

        self.strategy_config = strategy_config if strategy_config is not None else {}

    # Методы, которые должна определить каждая стратегия

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