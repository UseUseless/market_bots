import pandas as pd
from queue import Queue
from typing import Dict, Any, Optional

from core.event import SignalEvent
from strategies.base_strategy import BaseStrategy


class TripleFilterStrategy(BaseStrategy):
    """
    Реализация интрадей-стратегии "Тройной Фильтр" Александра Элдера.
    """

    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None,
                 risk_manager_type: str = "FIXED", risk_config: Optional[Dict[str, Any]] = None):
        _strategy_config = strategy_config if strategy_config is not None else {}
        strategy_params = _strategy_config.get(self.__class__.__name__, {})

        self.ema_fast_period = strategy_params.get("ema_fast_period", 9)
        self.ema_slow_period = strategy_params.get("ema_slow_period", 21)
        self.ema_trend_period = strategy_params.get("ema_trend_period", 200)
        self.volume_sma_period = strategy_params.get("volume_sma_period", 20)

        self.min_history_needed = self.ema_trend_period + 1
        self.required_indicators = [
            {"name": "ema", "params": {"period": self.ema_fast_period}},
            {"name": "ema", "params": {"period": self.ema_slow_period}},
            {"name": "ema", "params": {"period": self.ema_trend_period}},
            {"name": "sma", "params": {"period": self.volume_sma_period, "column": "volume"}},
        ]

        super().__init__(events_queue, instrument, strategy_config, risk_manager_type, risk_config)

        self.ema_fast_name = f'EMA_{self.ema_fast_period}'
        self.ema_slow_name = f'EMA_{self.ema_slow_period}'
        self.ema_trend_name = f'EMA_{self.ema_trend_period}'
        self.volume_sma_name = f'SMA_{self.volume_sma_period}_volume'

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Анализирует последнюю свечу и генерирует сигнал BUY или SELL,
        если все три "фильтра" стратегии совпадают.
        """
        # --- Условия для сигнала на ПОКУПКУ ---
        buy_trend = last_candle['close'] > last_candle[self.ema_trend_name]
        buy_impulse = prev_candle[self.ema_fast_name] < prev_candle[self.ema_slow_name] and \
                      last_candle[self.ema_fast_name] > last_candle[self.ema_slow_name]
        buy_volume = last_candle['volume'] > last_candle[self.volume_sma_name]

        if buy_trend and buy_impulse and buy_volume:
            signal = SignalEvent(timestamp=timestamp, instrument=self.instrument, direction="BUY",
                                 strategy_id=self.name)
            self.events_queue.put(signal)
            return

        # --- Условия для сигнала на ПРОДАЖУ (закрытие лонга или вход в шорт) ---
        sell_trend = last_candle['close'] < last_candle[self.ema_trend_name]
        sell_impulse = prev_candle[self.ema_fast_name] > prev_candle[self.ema_slow_name] and \
                       last_candle[self.ema_fast_name] < last_candle[self.ema_slow_name]
        sell_volume = last_candle['volume'] > last_candle[self.volume_sma_name]

        if sell_trend and sell_impulse and sell_volume:
            signal = SignalEvent(timestamp=timestamp, instrument=self.instrument, direction="SELL",
                                 strategy_id=self.name)
            self.events_queue.put(signal)
            return