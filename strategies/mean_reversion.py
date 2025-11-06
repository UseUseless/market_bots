import pandas as pd
from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy
from config import STRATEGY_CONFIG


class MeanReversionStrategy(BaseStrategy):
    """
    Статистическая контртрендовая стратегия, основанная на Z-Score.
    - Входит в позицию, когда цена статистически значимо отклоняется от своего среднего.
    - Выходит из позиции, когда цена возвращается к среднему.
    """

    _config = STRATEGY_CONFIG["MeanReversionStrategy"]
    candle_interval: str = _config["candle_interval"]

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)

        self.sma_period = self._config["sma_period"]
        self.upper_threshold = self._config["z_score_upper_threshold"]
        self.lower_threshold = self._config["z_score_lower_threshold"]
        self.min_history_needed = self.sma_period + 1

        # Декларируем потребность в SMA
        self.required_indicators = [
            {"name": "sma", "params": {"period": self.sma_period}},
        ]

        self.data_history = []

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Рассчитывает стандартное отклонение и Z-Score.
        SMA уже рассчитан FeatureEngine.
        """
        logging.info(f"Стратегия '{self.name}' рассчитывает кастомные фичи (STD, Z-Score)...")

        # Имя колонки SMA берем то, что сгенерировал FeatureEngine
        sma_col_name = f'SMA_{self.sma_period}'

        # Проверяем, что FeatureEngine действительно добавил нужную колонку
        if sma_col_name not in data.columns:
            logging.error(f" колонка {sma_col_name} не найдена в данных. Расчет Z-Score невозможен.")
            return pd.DataFrame()  # Возвращаем пустой DataFrame, чтобы остановить бэктест

        # 1. Расчет скользящего стандартного отклонения (STD)
        std_dev = data['close'].rolling(window=self.sma_period).std()

        # 2. Расчет Z-Score, используя уже готовую SMA
        data['z_score'] = (data['close'] - data[sma_col_name]) / std_dev

        return data

    def calculate_signals(self, event: MarketEvent):
        """Генерирует сигналы на вход и выход на основе Z-Score."""
        self.data_history.append(event.data)
        if len(self.data_history) > 2:
            self.data_history.pop(0)
        if len(self.data_history) < 2:
            return

        last = self.data_history[-1]
        prev = self.data_history[-2]

        # Проверяем, что z_score вообще есть в данных
        if 'z_score' not in last or 'z_score' not in prev:
            return

        # --- Сигналы на ВХОД ---
        if prev['z_score'] < self.lower_threshold and last['z_score'] > self.lower_threshold:
            logging.info(f"СИГНАЛ BUY: Z-Score ({last['z_score']:.2f}) пересек порог {self.lower_threshold}")
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
            return

        if prev['z_score'] > self.upper_threshold and last['z_score'] < self.upper_threshold:
            logging.info(f"СИГНАЛ SELL: Z-Score ({last['z_score']:.2f}) пересек порог {self.upper_threshold}")
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
            return

        # --- Сигналы на ВЫХОД (возврат к среднему) ---
        if prev['z_score'] < 0 and last['z_score'] > 0:
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
            return

        if prev['z_score'] > 0 and last['z_score'] < 0:
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
            return