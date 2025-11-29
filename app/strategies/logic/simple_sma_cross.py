"""
Стратегия пересечения цены и скользящей средней (Price/SMA Crossover).

Классическая трендовая стратегия. Генерирует сигналы, когда цена пересекает
линию простой скользящей средней (SMA).
"""

from queue import Queue
import pandas as pd

from app.shared.schemas import StrategyConfigModel
from app.strategies.base_strategy import BaseStrategy
from app.shared.events import SignalEvent
from app.core.calculations.indicators import FeatureEngine
from app.shared.primitives import TradeDirection


class SimpleSMACrossStrategy(BaseStrategy):
    """
    Реализация стратегии Simple SMA Crossover.

    Логика сигналов:
    - **BUY (Long):** Цена закрытия пересекает SMA снизу вверх.
    - **SELL (Short/Close):** Цена закрытия пересекает SMA сверху вниз.

    Attributes:
        params_config (dict): Определение параметров для UI и оптимизатора.
    """

    # Конфигурация параметров для динамического создания UI и оптимизации
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
        """
        Инициализация стратегии и настройка индикаторов.

        Args:
            events_queue: Очередь для отправки сигналов.
            feature_engine: Движок расчета индикаторов.
            config: Параметры запуска (период SMA, инструмент и т.д.).
        """
        # 1. Извлекаем параметры из конфига
        self.sma_period = config.params["sma_period"]

        # 2. Формируем требования к данным
        # Нам нужно истории минимум на длину SMA + 1 свеча (для кроссовера)
        self.min_history_needed = self.sma_period + 1

        # Сообщаем FeatureEngine, что нам нужен расчет SMA
        self.required_indicators = [{"name": "sma", "params": {"period": self.sma_period}}]

        # Кэшируем имя колонки, которое создаст FeatureEngine (например, "SMA_50")
        self.sma_name = f"SMA_{self.sma_period}"

        # 3. Инициализируем родительский класс
        super().__init__(events_queue, feature_engine, config)

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Анализирует две последние свечи на предмет пересечения SMA.

        Args:
            prev_candle (pd.Series): Предыдущая свеча (t-1).
            last_candle (pd.Series): Текущая закрытая свеча (t).
            timestamp (pd.Timestamp): Время закрытия текущей свечи.
        """
        # Получаем значения цены и индикатора
        prev_close = prev_candle['close']
        curr_close = last_candle['close']

        prev_sma = prev_candle[self.sma_name]
        curr_sma = last_candle[self.sma_name]

        # Логика входа в LONG (Золотое пересечение цены)
        # Было ниже SMA, стало выше SMA
        if prev_close < prev_sma and curr_close > curr_sma:
            self.events_queue.put(SignalEvent(
                timestamp=timestamp,
                instrument=self.instrument,
                direction=TradeDirection.BUY,
                strategy_id=self.name,
                price=curr_close,
                interval=self.config.interval
            ))

        # Логика входа в SHORT или закрытия LONG (Смертельное пересечение)
        # Было выше SMA, стало ниже SMA
        elif prev_close > prev_sma and curr_close < curr_sma:
            self.events_queue.put(SignalEvent(
                timestamp=timestamp,
                instrument=self.instrument,
                direction=TradeDirection.SELL,
                strategy_id=self.name,
                price=curr_close,
                interval=self.config.interval
            ))