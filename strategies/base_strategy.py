from queue import Queue
import pandas as pd

from core.event import MarketEvent

class BaseStrategy:
    """
    Абстрактный базовый класс для всех торговых стратегий.
    Определяет "контракт", которому должна следовать каждая стратегия:
    - Предоставлять информацию о себе (figi, interval, sl/tp).
    - Уметь подготавливать данные (рассчитывать индикаторы).
    - Уметь генерировать сигналы на основе рыночных данных.
    """
    def __init__(self, events_queue: Queue, figi: str):
        self.events_queue = events_queue
        self.name: str = "Base"
        self.figi: str = figi 
        
        # --- КОНТРАКТ: Атрибуты, которые должна определить каждая стратегия ---
        self.candle_interval: str = ""
        self.stop_loss_percent: float = 0.0
        self.take_profit_percent: float = 0.0
        # --------------------------------------------------------------------

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Метод для подготовки данных. Стратегия сама добавляет
        в DataFrame необходимые ей индикаторы.
        Должен быть реализован в дочернем классе.
        """
        raise NotImplementedError("Метод prepare_data должен быть реализован в дочернем классе.")

    def calculate_signals(self, event: MarketEvent):
        """
        Основной метод, который анализирует рыночные данные (MarketEvent)
        и генерирует торговые сигналы (SignalEvent).
        Должен быть реализован в дочернем классе.
        """
        raise NotImplementedError("Метод calculate_signals должен быть реализован в дочернем классе.")