from dataclasses import dataclass
from datetime import datetime
import pandas as pd

@dataclass
class Event:
    """
    Базовый класс для всех событий.
    Служит для типизации и как родительский класс.
    """
    pass

@dataclass
class MarketEvent(Event):
    """
    Событие поступления новых рыночных данных (одной свечи).
    Генерируется DataHandler'ом.
    """
    timestamp: datetime
    instrument: str
    data: pd.Series # Строка DataFrame с ценами (OHLCV) и индикаторами

@dataclass
class SignalEvent(Event):
    """
    Событие генерации торгового сигнала стратегией.
    Генерируется Strategy.
    """
    timestamp: datetime
    instrument: str
    direction: str  # 'BUY' или 'SELL'

    # На будущее, если будут одновременно работать две стратегии,
    # чтобы не перекрывали сигналы друг друга, а исполнялись по отдельности
    # и не проходили проверки друг за друга
    strategy_id: str # Имя стратегии, сгенерировавшей сигнал

@dataclass
class OrderEvent(Event):
    """
    Событие для отправки ордера на исполнение.
    Генерируется Portfolio.
    """
    timestamp: datetime
    instrument: str
    quantity: int
    direction: str
    trigger_reason: str  # 'SIGNAL', 'SL', 'TP'

@dataclass
class FillEvent(Event):
    """
    Событие фактического исполнения ордера на бирже (или в симуляторе).
    Генерируется ExecutionHandler'ом.
    """
    timestamp: datetime
    instrument: str
    quantity: int
    direction: str  # 'BUY' или 'SELL'
    price: float    # Фактическая цена исполнения
    commission: float # Комиссия за сделку
    trigger_reason: str