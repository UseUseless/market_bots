import pandas as pd
from queue import Queue
import logging
from typing import Dict, Any, Optional, List

from core.event import SignalEvent
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger('backtester')

class MeanReversionStrategy(BaseStrategy):
    """
    Статистическая контртрендовая стратегия, основанная на Z-Score.
    """

    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None,
                 risk_manager_type: str = "FIXED", risk_config: Optional[Dict[str, Any]] = None):

        _strategy_config = strategy_config if strategy_config is not None else {}
        strategy_params = _strategy_config.get(self.__class__.__name__, {})

        self.sma_period = strategy_params.get("sma_period", 20)
        self.upper_threshold = strategy_params.get("z_score_upper_threshold", 2.0)
        self.lower_threshold = strategy_params.get("z_score_lower_threshold", -2.0)

        self.min_history_needed = self.sma_period + 1
        self.required_indicators = [
            {"name": "sma", "params": {"period": self.sma_period}},
        ]

        super().__init__(events_queue, instrument, strategy_config, risk_manager_type, risk_config)

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Рассчитывает кастомный индикатор Z-Score поверх стандартных индикаторов.
        """
        logger.info(f"Стратегия '{self.name}' рассчитывает кастомные фичи (STD, Z-Score)...")
        sma_col_name = f'SMA_{self.sma_period}'
        if sma_col_name not in data.columns:
            logger.error(f"Колонка {sma_col_name} не найдена. Расчет Z-Score невозможен.")
            return pd.DataFrame()

        std_dev = data['close'].rolling(window=self.sma_period).std()
        data['z_score'] = (data['close'] - data[sma_col_name]) / std_dev

        self._required_cols.append('z_score')
        return data

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        current_z_score = last_candle['z_score']
        prev_z_score = prev_candle['z_score']

        if prev_z_score < self.lower_threshold and current_z_score >= self.lower_threshold:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, "BUY", self.name))
        elif prev_z_score > self.upper_threshold and current_z_score <= self.upper_threshold:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, "SELL", self.name))
        elif prev_z_score < 0 and current_z_score >= 0:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, "SELL", self.name))
        elif prev_z_score > 0 and current_z_score <= 0:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, "BUY", self.name))