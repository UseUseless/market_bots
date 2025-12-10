"""
События системы (Events).

Облегченная версия. События передают только "дельту" изменений.
Контекст (какая стратегия, какая биржа) хранится в TradingConfig.
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
    instrument: str # Нужен, т.к. данные приходят асинхронно
    data: pd.Series


@dataclass
class SignalEvent(Event):
    """
    Стратегия хочет войти или выйти.
    Это "сырое" желание, еще не проверенное на риски.
    """
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    price: float  # Цена Close свечи, на которой возник сигнал


@dataclass
class OrderEvent(Event):
    """
    Приказ на исполнение (после риск-менеджмента).
    Содержит уже рассчитанный объем и уровни защиты.
    """
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    quantity: float
    trigger_reason: TriggerReason

    # Целевые уровни (для ордера входа)
    stop_loss: float = 0.0
    take_profit: float = 0.0

    # Цена исполнения (для лимиток или симуляции)
    price: Optional[float] = None


@dataclass
class FillEvent(Event):
    """
    Факт исполнения сделки.
    То, что реально произошло на бирже (симуляторе).
    """
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    quantity: float
    price: float
    commission: float
    trigger_reason: TriggerReason