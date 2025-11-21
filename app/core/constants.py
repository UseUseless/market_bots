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