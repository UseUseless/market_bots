"""
Модуль определений событий (Events).

Этот файл содержит классы событий, которые используются для асинхронного обмена
данными между компонентами системы через шину событий (Event Bus) или очереди (Queue).

Жизненный цикл торговой операции:
1.  **MarketEvent**: Пришли новые данные (свеча).
2.  **SignalEvent**: Стратегия проанализировала данные и захотела купить/продать.
3.  **OrderEvent**: Риск-менеджер одобрил сигнал, рассчитал объем и создал ордер.
4.  **FillEvent**: Биржа (или симулятор) исполнила ордер.

Использование `@dataclass` обеспечивает высокую производительность и читаемость.
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
        timestamp (datetime): Время закрытия свечи (UTC).
        instrument (str): Тикер инструмента (например, 'BTCUSDT').
        data (pd.Series): Полная строка данных свечи (OHLCV) плюс
            рассчитанные индикаторы. Стратегия использует именно это поле.
    """
    timestamp: datetime
    instrument: str
    data: pd.Series


@dataclass
class SignalEvent(Event):
    """
    Событие 'Торговый Сигнал' (Намерение).

    Генерируется: Strategy.
    Потребители: PortfolioManager / OrderManager.

    Это еще НЕ ордер. Это декларация намерения ("Я хочу купить").
    Сигнал может быть отфильтрован риск-менеджером (например, из-за превышения
    лимита потерь) или скорректирован.

    Attributes:
        timestamp (datetime): Время генерации сигнала.
        instrument (str): Тикер инструмента.
        direction (TradeDirection): Направление (BUY/SELL).
        strategy_id (str): Идентификатор стратегии (для логов и статистики).
        price (Optional[float]): Рекомендуемая цена входа (обычно Close свечи).
                                 Если None — предполагается вход по рынку.
        interval (str): Таймфрейм, на котором получен сигнал (для логов).
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
    Событие 'Ордер' (Команда).

    Генерируется: OrderManager / RiskMonitor.
    Потребители: ExecutionHandler (Simulator или Live).

    Это валидированная команда на исполнение. Риск-менеджмент пройден,
    размер позиции (quantity) рассчитан и округлен под требования биржи.

    Attributes:
        timestamp (datetime): Время создания ордера.
        instrument (str): Тикер инструмента.
        quantity (float): Точное количество лотов/монет для отправки на биржу.
        direction (TradeDirection): Направление сделки.
        trigger_reason (TriggerReason): Причина ордера (Сигнал стратегии, Стоп-лосс, Тейк-профит).
        stop_loss (float): Рассчитанный уровень SL (для установки на бирже, если поддерживается).
        take_profit (float): Рассчитанный уровень TP.
        price_hint (Optional[float]): "Подсказка" цены для симулятора.
            Используется в бэктестах, чтобы Simulator знал, от какой цены считать
            проскальзывание, не заглядывая в будущее. Обычно это Close текущей свечи.
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
    Событие 'Исполнение' (Факт).

    Генерируется: ExecutionHandler.
    Потребители: PortfolioManager (для обновления баланса и позиций).

    Описывает, что *фактически* произошло на бирже.

    Attributes:
        timestamp (datetime): Фактическое время исполнения сделки.
        instrument (str): Тикер.
        quantity (float): Фактически исполненный объем.
        direction (TradeDirection): Направление.
        price (float): Средняя цена исполнения (с учетом проскальзывания).
        commission (float): Удержанная комиссия (в валюте котировки).
        trigger_reason (TriggerReason): Причина, по которой произошла сделка.
        stop_loss (float): Уровень SL, привязанный к этой позиции (для учета в стейте).
        take_profit (float): Уровень TP, привязанный к этой позиции.
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