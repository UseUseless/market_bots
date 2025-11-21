from queue import Queue
import pandas as pd
from typing import Dict, Any, Optional

from app.core.models.event import SignalEvent
from app.strategies.base_strategy import BaseStrategy
from app.core.services.feature_engine import FeatureEngine
from app.core.constants import TradeDirection

class LiveDebugStrategy(BaseStrategy):
    """
    Стратегия для отладки.
    Генерирует сигнал BUY на каждой четной свече и SELL на каждой нечетной.
    Нужна, чтобы проверить работу пайплайна оповещений.
    """
    params_config = {
        "candle_interval": {"type": "str", "default": "1min", "optimizable": False}
    }
    # Нам не нужны индикаторы, но добавим SMA для проверки работы DataFeed
    required_indicators = [{"name": "sma", "params": {"period": 5}}]
    min_history_needed = 10

    def __init__(self, events_queue: Queue, instrument: str, params: Dict[str, Any],
                 feature_engine: FeatureEngine, risk_manager_type: str,
                 risk_manager_params: Optional[Dict[str, Any]] = None):
        super().__init__(events_queue, instrument, params, feature_engine, risk_manager_type, risk_manager_params)
        self.counter = 0

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        self.counter += 1

        # Проверяем, что индикатор реально посчитался (DataFeed работает)
        sma_val = last_candle.get('SMA_5')

        direction = TradeDirection.BUY if self.counter % 2 == 0 else TradeDirection.SELL

        print(f"\n[DEBUG STRATEGY] Свеча получена! Close: {last_candle['close']}, SMA_5: {sma_val}")
        print(f"[DEBUG STRATEGY] Генерирую тестовый сигнал {direction}...\n")

        signal = SignalEvent(
            timestamp=timestamp,
            instrument=self.instrument,
            direction=direction,
            strategy_id=self.name
        )
        self.events_queue.put(signal)