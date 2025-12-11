"""
Базовые примитивы данных.

Содержит перечисления (Enums) и структуры данных (Dataclasses),
которые используются во всех слоях приложения.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional


class ExchangeType(StrEnum):
    """
    Поддерживаемые биржи.
    Используется для выбора адаптера и валидации конфига.
    """
    TINKOFF = "tinkoff"
    BYBIT = "bybit"


class TradeDirection(StrEnum):
    """Направление позиции."""
    BUY = "BUY"
    SELL = "SELL"


class TriggerReason(StrEnum):
    """Причина генерации события (для аналитики)."""
    SIGNAL = "SIGNAL"       # Штатный вход/выход по стратегии
    STOP_LOSS = "SL"        # Сработал защитный стоп
    TAKE_PROFIT = "TP"      # Сработал тейк-профит


@dataclass
class TradeRiskProfile:
    """
    Результат расчета риск-менеджера.
    Используется для передачи параметров от RiskManager к Portfolio.
    """
    stop_loss_price: float
    take_profit_price: float
    quantity: float
    risk_amount: float  # Денежный риск на сделку


@dataclass
class Trade:
    """
    Единая сущность сделки.
    Используется и как активная позиция (в памяти), и как запись в журнале (на диске).
    """
    # Идентификация
    id: str  # UUID или уникальный хеш
    symbol: str
    direction: TradeDirection

    # --- ВХОД (Entry) ---
    entry_time: datetime
    entry_price: float
    quantity: float
    entry_commission: float = 0.0

    # --- РИСК (Metadata) ---
    stop_loss: float = 0.0
    take_profit: float = 0.0

    # --- ВЫХОД (Exit) - Заполняется при закрытии ---
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_commission: Optional[float] = 0.0
    exit_reason: Optional[TriggerReason] = None

    # --- РЕЗУЛЬТАТ ---
    pnl: float = 0.0        # Чистый PnL
    pnl_pct: float = 0.0    # PnL в процентах

    @property
    def is_closed(self) -> bool:
        """Позиция закрыта, если есть время выхода."""
        return self.exit_time is not None

    def close(self, exit_time: datetime, exit_price: float, reason: TriggerReason, commission: float = 0.0):
        """
        Метод для закрытия сделки и расчета PnL.

        Args:
            exit_time: Время выхода.
            exit_price: Цена исполнения выхода.
            reason: Причина выхода (SL/TP/Signal).
            commission: Комиссия за выход.
        """
        self.exit_time = exit_time
        self.exit_price = exit_price
        self.exit_reason = reason
        self.exit_commission = commission

        # Расчет Валовой Прибыли (Gross PnL)
        gross_pnl = 0.0
        if self.direction == TradeDirection.BUY:
            gross_pnl = (self.exit_price - self.entry_price) * self.quantity
        else:
            gross_pnl = (self.entry_price - self.exit_price) * self.quantity

        # Чистая Прибыль (Net PnL)
        self.pnl = gross_pnl - self.entry_commission - self.exit_commission

        # Расчет процентов (ROI)
        invested = self.entry_price * self.quantity
        if invested > 0:
            self.pnl_pct = (self.pnl / invested) * 100