"""
Модуль базовых примитивов данных.

Содержит перечисления (Enums) и структуры данных (Dataclasses), которые
являются атомарными строительными блоками для бизнес-логики приложения.
Они используются для типизации аргументов функций, событий и состояния портфеля.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TradeDirection(StrEnum):
    """
    Направление торговой операции или позиции.
    Наследуется от str, что позволяет сериализовать значения в JSON/БД как "BUY"/"SELL".
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
    Агрегат, описывающий состояние одной открытой позиции.

    Хранится в `PortfolioState`. Создается при получении FillEvent на открытие,
    удаляется при получении FillEvent на закрытие.

    Attributes:
        instrument (str): Тикер инструмента (например, 'BTCUSDT' или 'SBER').
        quantity (float): Текущий объем позиции. Для акций — лоты, для крипты — монеты.
        entry_price (float): Средневзвешенная цена входа (с учетом комиссий и проскальзывания, если применимо).
        entry_timestamp (datetime): Время открытия позиции (UTC).
        direction (TradeDirection): Направление (Long/Short).
        stop_loss (float): Текущий уровень Stop Loss (абсолютная цена).
        take_profit (float): Текущий уровень Take Profit (абсолютная цена).
        entry_commission (float): Комиссия, уплаченная при входе (для расчета чистого PnL при выходе).
    """
    instrument: str
    quantity: float
    entry_price: float
    entry_timestamp: datetime
    direction: TradeDirection
    stop_loss: float
    take_profit: float
    entry_commission: float


@dataclass
class TradeRiskProfile:
    """
    Результат расчета риск-менеджера для потенциальной сделки.

    Этот объект передается из `RiskManager` в `PositionSizer`.
    Он инкапсулирует параметры риска, отделяя логику "где выходить"
    от логики "сколько покупать".

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