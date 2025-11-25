from dataclasses import dataclass
from datetime import datetime
import pandas as pd
from typing import Optional

from app.core.constants import TradeDirection, TriggerReason


@dataclass
class Event:
    """
    Базовый (родительский) класс-маркер для всех событий в системе.
    Позволяет типизировать очереди (Queue[Event]) и создавать универсальные обработчики.
    """
    pass


@dataclass
class MarketEvent(Event):
    """
    Событие поступления новых рыночных данных (закрытие свечи).

    Триггер для всей системы. Когда появляется это событие,
    стратегии начинают расчет, а риск-монитор проверяет стоп-лоссы.
    """
    timestamp: datetime  # Время закрытия свечи (UTC)
    instrument: str  # Тикер (напр. BTCUSDT)
    data: pd.Series  # Данные свечи (OHLCV) + рассчитанные индикаторы


@dataclass
class SignalEvent(Event):
    """
    Событие "Торговый Сигнал". Намерение стратегии совершить сделку.

    Это еще не ордер! Сигнал может быть отклонен Риск-менеджером
    или фильтром волатильности.
    """
    timestamp: datetime
    instrument: str
    direction: TradeDirection
    strategy_id: str  # Кто сгенерировал сигнал
    price: Optional[float] = None  # Справочная цена (обычно Close свечи)
    interval: str = None  # Таймфрейм


@dataclass
class OrderEvent(Event):
    """
    Событие "Ордер". Валидированная команда на исполнение.
    Прошла проверки риск-менеджмента, сайзинга и фильтров.
    """
    timestamp: datetime
    instrument: str
    quantity: float  # Точное количество лотов для биржи
    direction: TradeDirection
    trigger_reason: TriggerReason  # SIGNAL, STOP_LOSS или TAKE_PROFIT
    stop_loss: float = 0.0  # Уровень SL для установки в ордер (если биржа поддерживает)
    take_profit: float = 0.0  # Уровень TP
    price_hint: Optional[float] = None  # Ожидаемая цена (для симулятора, чтобы избежать проскальзывания в бэктесте)


@dataclass
class FillEvent(Event):
    """
    Событие "Исполнение". Подтвержденный факт сделки.
    """
    timestamp: datetime
    instrument: str
    quantity: float
    direction: TradeDirection
    price: float  # Фактическая цена исполнения
    commission: float  # Фактическая комиссия
    trigger_reason: TriggerReason
    stop_loss: float = 0.0
    take_profit: float = 0.0