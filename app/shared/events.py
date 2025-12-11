"""
События системы (Events).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pandas as pd

from app.shared.primitives import TradeDirection, TriggerReason


@dataclass
class Event:
    pass


@dataclass
class MarketEvent(Event):
    """Пришла новая свеча."""
    timestamp: datetime
    instrument: str
    data: pd.Series


@dataclass
class SignalEvent(Event):
    """
    Стратегия хочет войти или выйти.
    """
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    price: float
    strategy_name: str


@dataclass
class OrderEvent(Event):
    """Приказ на исполнение."""
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    quantity: float
    trigger_reason: TriggerReason
    stop_loss: float = 0.0
    take_profit: float = 0.0
    price: Optional[float] = None


@dataclass
class FillEvent(Event):
    """Факт исполнения сделки."""
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    quantity: float
    price: float
    commission: float
    trigger_reason: TriggerReason
    stop_loss: float = 0.0
    take_profit: float = 0.0