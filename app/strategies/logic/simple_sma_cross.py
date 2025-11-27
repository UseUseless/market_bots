from queue import Queue
import pandas as pd

from app.shared.schemas import StrategyConfigModel
from app.strategies.base_strategy import BaseStrategy
from app.shared.events import SignalEvent
from app.core.calculations.indicators import FeatureEngine
from app.shared.primitives import TradeDirection


class SimpleSMACrossStrategy(BaseStrategy):
    """
    Простая стратегия на пересечении ценой скользящей средней.
    Параметры считываются из переданного конфига.
    """

    params_config = {
        "sma_period": {
            "type": "int",
            "default": 50,
            "optimizable": True,
            "low": 10,
            "high": 100,
            "step": 1,
            "description": "Период скользящей средней."
        },
        "candle_interval": {
            "type": "str",
            "default": "1hour",
            "optimizable": False,
            "description": "Рекомендуемый таймфрейм для стратегии."
        }
    }

    def __init__(self,
                 events_queue: Queue,
                 feature_engine: FeatureEngine,
                 config: StrategyConfigModel):

        # 1. Сначала достаем параметры из конфига для настройки индикаторов
        self.sma_period = config.params["sma_period"]

        # 2. Настраиваем индикаторы
        self.min_history_needed = self.sma_period + 1
        self.required_indicators = [{"name": "sma", "params": {"period": self.sma_period}}]
        self.sma_name = f"SMA_{self.sma_period}"

        # 3. Инициализируем базовый класс
        super().__init__(events_queue, feature_engine, config)

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Генерирует сигнал, если цена закрытия пересекла SMA.
        """
        # Сигнал на покупку: цена пересекла SMA снизу вверх
        if prev_candle['close'] < prev_candle[self.sma_name] and last_candle['close'] > last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, TradeDirection.BUY, self.name))

        # Сигнал на продажу (закрытие лонга): цена пересекла SMA сверху вниз
        elif prev_candle['close'] > prev_candle[self.sma_name] and last_candle['close'] < last_candle[self.sma_name]:
            self.events_queue.put(SignalEvent(timestamp, self.instrument, TradeDirection.SELL, self.name))