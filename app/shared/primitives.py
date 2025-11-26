from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

class TradeDirection(StrEnum):
    """Направление торговли."""
    BUY = "BUY"
    SELL = "SELL"


class TriggerReason(StrEnum):
    """Причина создания/исполнения ордера."""
    SIGNAL = "SIGNAL"
    STOP_LOSS = "SL"
    TAKE_PROFIT = "TP"


class ExchangeType(StrEnum):
    """Поддерживаемые биржи."""
    TINKOFF = "tinkoff"
    BYBIT = "bybit"

@dataclass
class Position:
    """
    Структура данных для хранения полной информации об одной открытой позиции.

    Использование dataclass обеспечивает строгую типизацию и упрощает создание
    объектов, делая код более чистым и менее подверженным ошибкам по сравнению
    с использованием словарей.
    """

    instrument: str
    """Тикер или символ инструмента, например, 'SBER' или 'BTCUSDT'."""

    quantity: float
    """
    Количество купленных/проданных единиц (акций, контрактов, монет).
    Может быть дробным для криптовалют.
    """

    entry_price: float
    """Фактическая цена входа в позицию с учетом проскальзывания."""

    entry_timestamp: datetime
    """Временная метка (UTC) свечи, на которой была открыта позиция."""

    direction: TradeDirection  # 'BUY' или 'SELL'
    """Направление позиции: 'BUY' для длинной, 'SELL' для короткой."""

    stop_loss: float
    """Абсолютный уровень цены для стоп-лосса."""

    take_profit: float
    """Абсолютный уровень цены для тейк-профита."""

    entry_commission: float
    """Комиссия, уплаченная при открытии этой позиции."""


@dataclass
class TradeRiskProfile:
    """
    Структура данных, которая инкапсулирует все параметры риска для одной сделки.
    Причина создания этого класса - уйти от передачи множества отдельных аргументов
    (sl, tp, risk_amount...) между функциями. Теперь мы передаем один понятный объект всегда.
    """
    stop_loss_price: float      # Абсолютная цена стоп-лосса
    take_profit_price: float    # Абсолютная цена тейк-профита
    risk_per_share: float       # Количество денег, которым рискуем на 1 акцию (abs(entry - stop))
    risk_amount: float          # Сумма денег, которой рискуем на сделке (процент от капитала)
