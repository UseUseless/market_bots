"""
Модуль определений событий (Events).

Этот файл содержит классы событий, которые используются для обмена
данными между компонентами системы через шину событий (Event Bus) или очереди (Queue).

Жизненный цикл торговой операции:
1.  **MarketEvent**: Пришли новые данные (свеча).
2.  **SignalEvent**: Стратегия проанализировала данные и захотела купить/продать.
3.  **OrderEvent**: Риск-менеджер одобрил сигнал, рассчитал объем и создал ордер.
4.  **FillEvent**: Симулятор исполнил ордер.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pandas as pd

from app.shared.primitives import TradeDirection, TriggerReason


@dataclass
class Event:
    """
    Базовый класс-маркер для всех событий.

    Не содержит полей. Нужен для:
    1. Типизации очередей (например, `Queue[Event]`).
    2. Реализации полиморфизма (функции могут принимать любой Event).
    """
    pass


@dataclass
class MarketEvent(Event):
    """
    Событие обновления рыночных данных.

    Генерируется: DataFeed (Live или Backtest).
    Потребители: Strategy (для анализа), RiskMonitor (для проверки SL/TP).

    Attributes:
        data (pd.Series): Полная строка данных свечи (OHLCV) плюс
            рассчитанные индикаторы. Стратегия использует именно это поле.
        instrument (str): Тикер инструмента (например, 'BTCUSDT').
        timestamp (datetime): Время закрытия свечи (UTC).

    """
    data: pd.Series
    instrument: str
    timestamp: datetime


@dataclass
class SignalEvent(Event):
    """
    Событие 'Торговый Сигнал'.

    Генерируется: Strategy.
    Потребители: PortfolioManager / OrderManager.

    Это еще НЕ ордер. Это декларация намерения ("Я хочу купить").
    Сигнал может быть отфильтрован риск-менеджером (например, из-за превышения
    лимита потерь) или скорректирован.

    Attributes:
        instrument (str): Тикер инструмента.
        direction (TradeDirection): Направление (BUY/SELL).
        timestamp (datetime): Время генерации сигнала.
        strategy_id (str): Идентификатор стратегии (для логов и статистики).
        interval (str): Таймфрейм, на котором получен сигнал (для логов).
        price (Optional[float]): Рекомендуемая цена входа (обычно Close свечи).
                                 Если None — предполагается вход по рынку.
    """
    instrument: str
    direction: TradeDirection
    timestamp: datetime
    strategy_id: str
    interval: str = None
    price: Optional[float] = None

@dataclass
class OrderEvent(Event):
    """
    Событие 'Ордер'.

    Генерируется: OrderManager / RiskMonitor.
    Потребители: ExecutionHandler (Simulator или Live).

    Это валидированная команда на исполнение. Риск-менеджмент пройден,
    размер позиции (quantity) рассчитан и округлен под требования биржи.

    Attributes:
        instrument (str): Тикер инструмента.
        direction (TradeDirection): Направление сделки.
        timestamp (datetime): Время создания ордера.
        trigger_reason (TriggerReason): Причина ордера (Сигнал стратегии, Стоп-лосс, Тейк-профит).
        quantity (float): Точное количество лотов/монет для отправки на биржу.
        stop_loss (float): Рассчитанный уровень SL.
        take_profit (float): Рассчитанный уровень TP.
        price (Optional[float]): Цена без проскальзывания для Sl/TP. Обычно это Close текущей свечи.
    """
    instrument: str
    direction: TradeDirection
    timestamp: datetime
    trigger_reason: TriggerReason
    quantity: float
    price: Optional[float] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0


@dataclass
class FillEvent(Event):
    """
    Событие 'Исполнение'.

    Генерируется: ExecutionHandler.
    Потребители: PortfolioManager (для обновления баланса и позиций).

    Описывает, что *фактически* произошло на бирже.

    Attributes:
        instrument (str): Тикер инструмента.
        direction (TradeDirection): Направление.
        timestamp (datetime): Фактическое время исполнения сделки.
        trigger_reason (TriggerReason): Причина, по которой произошла сделка.
        quantity (float): Фактически исполненный объем.
        commission (float): Удержанная комиссия (в валюте котировки).
        price (float): Средняя цена исполнения (с учетом проскальзывания).
        stop_loss (float): Уровень SL, привязанный к этой позиции (для учета в стейте).
        take_profit (float): Уровень TP, привязанный к этой позиции.
    """
    instrument: str
    direction: TradeDirection
    timestamp: datetime
    trigger_reason: TriggerReason
    quantity: float
    commission: float
    price: float
    stop_loss: float = 0.0
    take_profit: float = 0.0