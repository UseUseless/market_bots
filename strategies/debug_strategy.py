import pandas as pd
from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy


class TestSignalStrategy(BaseStrategy):
    """
    Генерирует ОДИН сигнал BUY на 10-й свече для проверки входа и авто-выхода.
    """
    candle_interval: str = "1s" # Ускорим для теста
    min_history_needed: int = 1

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        self.bar_index = -1
        self.signal_sent = False

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return data

    def calculate_signals(self, event: MarketEvent):
        self.bar_index += 1
        logging.debug(f"TestSignalStrategy: Свеча #{self.bar_index}")

        if not self.signal_sent and self.bar_index == 10:
            logging.warning(">>> TestSignalStrategy: ГЕНЕРАЦИЯ СИГНАЛА BUY <<<")
            signal = SignalEvent(instrument=self.instrument, direction="BUY", strategy_id=self.name)
            self.events_queue.put(signal)
            self.signal_sent = True