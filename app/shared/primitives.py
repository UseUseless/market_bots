"""
Модуль базовых примитивов данных.

Содержит перечисления (Enums) и структуры данных (Dataclasses).
Используются для типизации аргументов функций, событий и состояния портфеля.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TradeDirection(StrEnum):
    """
    Направление торговой операции или позиции.
    """
    BUY = "BUY"
    SELL = "SELL"


class TriggerReason(StrEnum):
    """
    Причина генерации события (сигнала или ордера).
    Используется для аналитики: позволяет понять, был ли выход из позиции плановым
    (по стратегии) или аварийным (Stop Loss).
    """
    SIGNAL = "SIGNAL"       # Штатный вход/выход по логике стратегии
    STOP_LOSS = "SL"        # Срабатывание защитного стоп-ордера
    TAKE_PROFIT = "TP"      # Срабатывание ордера фиксации прибыли


class ExchangeType(StrEnum):
    """
    Константы поддерживаемых бирж.
    Используются в фабриках и конфигах для выбора адаптера.
    """
    TINKOFF = "tinkoff"
    BYBIT = "bybit"


@dataclass
class Position:
    """
    Описывает состояние одной открытой позиции.

    Хранится в `PortfolioState`. Создается при получении FillEvent на открытие,
    удаляется при получении FillEvent на закрытие.

    Attributes:
        instrument (str): Тикер инструмента (например, 'BTCUSDT' или 'SBER').
        direction (TradeDirection): Направление (Long/Short).
        entry_timestamp (datetime): Время открытия позиции (UTC).
        quantity (float): Текущий объем позиции.
        entry_commission (float): Комиссия, уплаченная при входе (для расчета чистого PnL при выходе).
        entry_price (float): Цена входа.
        stop_loss (float): Текущий уровень Stop Loss.
        take_profit (float): Текущий уровень Take Profit.
    """
    instrument: str
    direction: TradeDirection
    entry_timestamp: datetime
    quantity: float
    entry_commission: float
    entry_price: float
    stop_loss: float
    take_profit: float


@dataclass
class TradeRiskProfile:
    """
    Результат расчета риск-менеджера для потенциальной сделки.

    Этот объект передается из `RiskManager` в `PositionSizer`.

    Attributes:
        stop_loss_price (float): Рассчитанная цена стоп-лосса (уровень отмены сценария).
        take_profit_price (float): Рассчитанная цена тейк-профита (целевой уровень).
        risk_per_share (float): Денежный риск на 1 единицу актива.
                                Формула: `abs(entry_price - stop_loss_price)`.
        risk_amount (float): Общий допустимый денежный риск на всю сделку.
                             Обычно это % от текущего капитала (например, 1% от $1000 = $10).
    """
    stop_loss_price: float
    take_profit_price: float
    risk_per_share: float
    risk_amount: float