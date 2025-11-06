import pandas as pd
import pandas_ta as ta
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

    # Загружаем конфиг для этого класса
    _config = STRATEGY_CONFIG["MeanReversionStrategy"]
    candle_interval: str = _config["candle_interval"]

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)

        # Загружаем параметры из конфига
        self.sma_period = self._config["sma_period"]
        self.upper_threshold = self._config["z_score_upper_threshold"]
        self.lower_threshold = self._config["z_score_lower_threshold"]

        # Минимальное количество истории для расчета SMA и STD
        self.min_history_needed = self.sma_period + 1

        # Внутреннее состояние для отслеживания предыдущей свечи
        self.data_history = []

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Рассчитывает SMA, стандартное отклонение и Z-Score."""
        logging.info(f"Стратегия '{self.name}' рассчитывает Z-Score...")

        # 1. Расчет скользящей средней (SMA)
        sma = data.ta.sma(length=self.sma_period)

        # 2. Расчет скользящего стандартного отклонения (STD)
        std_dev = data['close'].rolling(window=self.sma_period).std()

        # 3. Расчет Z-Score
        data['z_score'] = (data['close'] - sma) / std_dev

        data.dropna(inplace=True)
        data.reset_index(drop=True, inplace=True)
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

        # --- Сигналы на ВХОД ---

        # Вход в ЛОНГ: Z-Score пересекает нижний порог снизу вверх
        if prev['z_score'] < self.lower_threshold and last['z_score'] > self.lower_threshold:
            logging.info(f"СИГНАЛ BUY: Z-Score ({last['z_score']:.2f}) пересек порог {self.lower_threshold}")
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
            return

        # Вход в ШОРТ: Z-Score пересекает верхний порог сверху вниз
        if prev['z_score'] > self.upper_threshold and last['z_score'] < self.upper_threshold:
            logging.info(f"СИГНАЛ SELL: Z-Score ({last['z_score']:.2f}) пересек порог {self.upper_threshold}")
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
            return

        # --- Сигналы на ВЫХОД (возврат к среднему) ---

        # Выход из ЛОНГА: Z-Score пересекает 0 снизу вверх
        if prev['z_score'] < 0 and last['z_score'] > 0:
            # Отправляем противоположный сигнал (SELL) для закрытия лонга
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
            return

        # Выход из ШОРТА: Z-Score пересекает 0 сверху вниз
        if prev['z_score'] > 0 and last['z_score'] < 0:
            # Отправляем противоположный сигнал (BUY) для закрытия шорта
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
            return