import pandas as pd
from queue import Queue
import logging

from app.shared.events import SignalEvent
from app.strategies.base_strategy import BaseStrategy
from app.shared.types import TradeDirection
from app.shared.schemas import TradingConfig

logger = logging.getLogger('backtester')


class MeanReversionStrategy(BaseStrategy):
    """
    Статистическая контртрендовая стратегия, основанная на Z-Score.
    """
    params_config = {
        "candle_interval": {
            "type": "str",
            "default": "15min",
            "optimizable": False,
            "description": "Рекомендуемый таймфрейм."
        },
        "sma_period": {
            "type": "int",
            "default": 20,
            "optimizable": True,
            "low": 10,
            "high": 50,
            "step": 1,
            "description": "Период для SMA и стандартного отклонения."
        },
        "z_score_upper_threshold": {
            "type": "float",
            "default": 2.0,
            "optimizable": True,
            "low": 1.5,
            "high": 3.0,
            "step": 0.1,
            "description": "Верхний порог Z-Score для входа в шорт."
        },
        "z_score_lower_threshold": {
            "type": "float",
            "default": -2.0,
            "optimizable": True,
            "low": -3.0,
            "high": -1.5,
            "step": 0.1,
            "description": "Нижний порог Z-Score для входа в лонг."
        }
    }

    def __init__(self, events_queue: Queue, config: TradingConfig):
        self.sma_period = config.strategy_params["sma_period"]
        self.upper_threshold = config.strategy_params["z_score_upper_threshold"]
        self.lower_threshold = config.strategy_params["z_score_lower_threshold"]

        # 2. Динамически формируем зависимости
        self.min_history_needed = self.sma_period + 1
        self.required_indicators = [
            {"name": "sma", "params": {"period": self.sma_period}},
        ]

        # 3. Вызываем родительский __init__
        super().__init__(events_queue, config)

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Рассчитывает кастомный индикатор Z-Score поверх стандартных индикаторов.
        """
        sma_col_name = f'SMA_{self.sma_period}'
        if sma_col_name not in data.columns:
            return data

        std_dev = data['close'].rolling(window=self.sma_period).std()

        if std_dev.iloc[-1] == 0:
            data['z_score'] = 0
        else:
            data['z_score'] = (data['close'] - data[sma_col_name]) / std_dev
        return data

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        current_z_score = last_candle['z_score']
        prev_z_score = prev_candle['z_score']

        # Сигнал на покупку (возврат к среднему снизу)
        if prev_z_score < self.lower_threshold and current_z_score >= self.lower_threshold:
            self.events_queue.put(SignalEvent(
                timestamp=timestamp,
                instrument=self.instrument,
                direction=TradeDirection.BUY,
                price=last_candle['close'],
                strategy_name=self.name
            ))

        # Сигнал на продажу (возврат к среднему сверху)
        elif prev_z_score > self.upper_threshold and current_z_score <= self.upper_threshold:
            self.events_queue.put(SignalEvent(
                timestamp=timestamp,
                instrument=self.instrument,
                direction=TradeDirection.SELL,
                price=last_candle['close'],
                strategy_name=self.name
            ))

        # Сигнал на закрытие лонга (пересечение нулевой линии)
        elif prev_z_score < 0 and current_z_score >= 0:
            self.events_queue.put(SignalEvent(
                timestamp=timestamp,
                instrument=self.instrument,
                direction=TradeDirection.SELL,
                price=last_candle['close'],
                strategy_name=self.name
            ))

        # Сигнал на закрытие шорта (пересечение нулевой линии)
        elif prev_z_score > 0 and current_z_score <= 0:
            self.events_queue.put(SignalEvent(
                timestamp=timestamp,
                instrument=self.instrument,
                direction=TradeDirection.BUY,
                price=last_candle['close'],
                strategy_name=self.name
            ))