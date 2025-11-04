import pandas as pd
from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy


class TestSignalStrategy(BaseStrategy):
    """
    Простая тестовая стратегия, которая генерирует сигнал BUY
    на 10-й свече после начала работы, а затем сигнал SELL на 20-й.
    Предназначена ИСКЛЮЧИТЕЛЬНО для отладки live-движка.
    """
    candle_interval: str = "1min"  # Рекомендуемый интервал

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        self.bar_index = -1
        self.buy_signal_sent = False
        self.sell_signal_sent = False

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Для этой стратегии подготовка данных не требуется."""
        # Просто возвращаем данные как есть
        return data

    def calculate_signals(self, event: MarketEvent):
        """Генерирует сигналы на 10-й и 20-й свече."""
        self.bar_index += 1
        logging.debug(f"TestSignalStrategy: Получена свеча #{self.bar_index}")

        # Генерируем сигнал на покупку один раз
        if not self.buy_signal_sent and self.bar_index == 10:
            logging.warning(">>> TestSignalStrategy: ГЕНЕРАЦИЯ СИГНАЛА BUY <<<")
            signal = SignalEvent(instrument=self.instrument, direction="BUY", strategy_id=self.name)
            self.events_queue.put(signal)
            self.buy_signal_sent = True

        # Генерируем сигнал на продажу (закрытие) один раз
        elif not self.sell_signal_sent and self.bar_index == 20:
            logging.warning(">>> TestSignalStrategy: ГЕНЕРАЦИЯ СИГНАЛА SELL <<<")
            signal = SignalEvent(instrument=self.instrument, direction="SELL", strategy_id=self.name)
            self.events_queue.put(signal)
            self.sell_signal_sent = True