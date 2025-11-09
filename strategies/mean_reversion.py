import pandas as pd
from queue import Queue
import logging
from typing import Dict, Any, Optional

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy


logger = logging.getLogger('backtester')

class MeanReversionStrategy(BaseStrategy):
    """
    Статистическая контртрендовая стратегия, основанная на Z-Score.
    - Входит в позицию, когда цена пересекает экстремальный уровень.
    - Выходит из позиции, когда цена возвращается к среднему (пересекает 0).
    """

    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None):
        super().__init__(events_queue, instrument, strategy_config)

        strategy_params = self.strategy_config.get(self.name, {})
        self.sma_period = strategy_params.get("sma_period", 20)
        self.upper_threshold = strategy_params.get("z_score_upper_threshold", 2.0)
        self.lower_threshold = strategy_params.get("z_score_lower_threshold", -2.0)
        self.min_history_needed = self.sma_period + 1

        self.required_indicators = [
            {"name": "sma", "params": {"period": self.sma_period}},
        ]

        self.prev_z_score = None

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Рассчитывает стандартное отклонение и Z-Score.
        SMA уже рассчитан FeatureEngine.
        """
        logger.info(f"Стратегия '{self.name}' рассчитывает кастомные фичи (STD, Z-Score)...")

        sma_col_name = f'SMA_{self.sma_period}'

        if sma_col_name not in data.columns:
            logger.error(f"Колонка {sma_col_name} не найдена в данных. Расчет Z-Score невозможен.")
            return pd.DataFrame()

        std_dev = data['close'].rolling(window=self.sma_period).std()
        data['z_score'] = (data['close'] - data[sma_col_name]) / std_dev

        return data

    def calculate_signals(self, event: MarketEvent):
        """
        Генерирует сигналы на вход и выход на основе ПЕРЕСЕЧЕНИЯ уровней Z-Score.
        """
        current_z_score = event.data.get('z_score')

        if pd.isna(current_z_score) or self.prev_z_score is None:
            self.prev_z_score = current_z_score
            return

        if self.prev_z_score < self.lower_threshold and current_z_score > self.lower_threshold:
            logger.info(
                f"СИГНАЛ BUY: Z-Score ({current_z_score:.2f}) пересек снизу вверх порог {self.lower_threshold}")
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "BUY", self.name))

        elif self.prev_z_score > self.upper_threshold and current_z_score < self.upper_threshold:
            logger.info(
                f"СИГНАЛ SELL: Z-Score ({current_z_score:.2f}) пересек сверху вниз порог {self.upper_threshold}")
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "SELL", self.name))

        elif self.prev_z_score < 0 and current_z_score > 0:
            logger.info(f"СИГНАЛ НА ЗАКРЫТИЕ ЛОНГА: Z-Score ({current_z_score:.2f}) пересек 0 снизу вверх.")
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "SELL", self.name))

        elif self.prev_z_score > 0 and current_z_score < 0:
            logger.info(f"СИГНАЛ НА ЗАКРЫТИЕ ШОРТА: Z-Score ({current_z_score:.2f}) пересек 0 сверху вниз.")
            self.events_queue.put(SignalEvent(event.timestamp, self.instrument, "BUY", self.name))

        # В конце обновляем значение для следующей итерации
        self.prev_z_score = current_z_score