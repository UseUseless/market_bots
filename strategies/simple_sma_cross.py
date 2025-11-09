from queue import Queue
import pandas as pd
from typing import Dict, Any, Optional

from strategies.base_strategy import BaseStrategy
from core.event import SignalEvent


class SimpleSMACrossStrategy(BaseStrategy):
    """
    Простая стратегия на пересечении ценой скользящей средней.
    Параметры считываются из переданного конфига.
    """

    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None,
                 risk_manager_type: str = "FIXED", risk_config: Optional[Dict[str, Any]] = None):
        _strategy_config = strategy_config if strategy_config is not None else {}
        strategy_params = _strategy_config.get(self.__class__.__name__, {})
        self.sma_period = strategy_params.get("sma_period", 50)

        self.min_history_needed = self.sma_period + 1
        self.required_indicators = [{"name": "sma", "params": {"period": self.sma_period}}]

        self.sma_name = f"SMA_{self.sma_period}"

        super().__init__(events_queue, instrument, strategy_config, risk_manager_type, risk_config)

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Генерирует сигнал, если цена закрытия пересекла SMA.
        """
        # Сигнал на покупку: цена пересекла SMA снизу вверх
        if prev_candle['close'] < prev_candle[self.sma_name] and last_candle['close'] > last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, "BUY", self.name))

        # Сигнал на продажу (закрытие лонга): цена пересекла SMA сверху вниз
        elif prev_candle['close'] > prev_candle[self.sma_name] and last_candle['close'] < last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, "SELL", self.name))