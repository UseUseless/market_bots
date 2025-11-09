from queue import Queue
import pandas as pd
from typing import Dict, Any, Optional

from strategies.base_strategy import BaseStrategy
from core.event import MarketEvent, SignalEvent


class SimpleSMACrossStrategy(BaseStrategy):
    """
    Простая стратегия на пересечении ценой скользящей средней.
    Параметры теперь считываются из переданного конфига.
    """

    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None):
        super().__init__(events_queue, instrument, strategy_config)

        strategy_params = self.strategy_config.get(self.name, {})  # Ищем секцию с именем нашего класса
        self.sma_period = strategy_params.get("sma_period", 50)
        # ----------------------

        # Динамически формируем требования и имена
        self.min_history_needed = self.sma_period + 1
        self.required_indicators = [{"name": "sma", "params": {"period": self.sma_period}}]
        self.sma_name = f"SMA_{self.sma_period}"

        self.data_history = []

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return data

    def calculate_signals(self, event: MarketEvent):
        self.data_history.append(event.data)
        if len(self.data_history) > 2:
            self.data_history.pop(0)

        if len(self.data_history) < 2:
            return

        last_candle = self.data_history[-1]
        prev_candle = self.data_history[-2]

        if self.sma_name not in last_candle:
            return

        if prev_candle['close'] < prev_candle[self.sma_name] and last_candle['close'] > last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "BUY", self.name))

        elif prev_candle['close'] > prev_candle[self.sma_name] and last_candle['close'] < last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "SELL", self.name))