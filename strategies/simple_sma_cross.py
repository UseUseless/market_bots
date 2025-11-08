from strategies.base_strategy import BaseStrategy
from core.event import MarketEvent, SignalEvent
import pandas as pd


class SimpleSMACrossStrategy(BaseStrategy):
    candle_interval = "1hour"
    min_history_needed = 51
    required_indicators = [{"name": "sma", "params": {"period": 50}}]

    def __init__(self, events_queue, instrument):
        super().__init__(events_queue, instrument)
        self.sma_name = "SMA_50"
        self.data_history = []

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return data

    def calculate_signals(self, event: MarketEvent):
        self.data_history.append(event.data)
        if len(self.data_history) < 2: return

        last_candle = self.data_history[-1]
        prev_candle = self.data_history[-2]

        if prev_candle['close'] < prev_candle[self.sma_name] and last_candle['close'] > last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "BUY", self.name))
        elif prev_candle['close'] > prev_candle[self.sma_name] and last_candle['close'] < last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "SELL", self.name))