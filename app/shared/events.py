"""
События системы (Events).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pandas as pd

from app.shared.types import TradeDirection, TriggerReason


@dataclass
class Event:
    """Базовый класс любых событий"""
    pass


@dataclass
class MarketEvent(Event):
    """Новая свеча."""
    timestamp: datetime
    instrument: str
    candle: pd.Series


@dataclass
class SignalEvent(Event):
    """
    Сигнал от стратегии на вход или выход.
    """
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    price: float
    strategy_name: str


@dataclass
class OrderEvent(Event):
    """Запрос исполнения заявки"""
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