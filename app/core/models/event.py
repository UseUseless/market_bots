from dataclasses import dataclass
from datetime import datetime
import pandas as pd
from typing import Optional

from app.core.constants import TradeDirection, TriggerReason

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
    data: pd.Series

@dataclass
class SignalEvent(Event):
    """
    Событие генерации торгового сигнала стратегией.
    Генерируется Strategy.
    """
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    strategy_id: str
    price: Optional[float] = None
    interval: str = None

@dataclass
class OrderEvent(Event):
    """
    Событие для отправки ордера на исполнение.
    Генерируется Portfolio.
    """
    timestamp: datetime
    instrument: str
    quantity: float
    direction: TradeDirection
    trigger_reason: TriggerReason
    stop_loss: float = 0.0
    take_profit: float = 0.0
    price_hint: Optional[float] = None

@dataclass
class FillEvent(Event):
    """
    Событие фактического исполнения ордера на бирже (или в симуляторе).
    Генерируется ExecutionHandler'ом.
    """
    timestamp: datetime
    instrument: str
    quantity: float
    direction: TradeDirection
    price: float
    commission: float
    trigger_reason: TriggerReason
    stop_loss: float = 0.0
    take_profit: float = 0.0