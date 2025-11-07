import pandas as pd
from queue import Queue

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy
from config import STRATEGY_CONFIG

class TripleFilterStrategy(BaseStrategy):
    """
    Реализация интрадей-стратегии "Тройной Фильтр" Александра Элдера.
    1. Тренд (EMA 200)
    2. Импульс (пересечение EMA 9 и EMA 21)
    3. Подтверждение объемом (Volume > SMA 20)
    """

    _config = STRATEGY_CONFIG["TripleFilterStrategy"]
    candle_interval: str = _config["candle_interval"]
    min_history_needed: int = _config["ema_trend_period"] + 1

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)

        self.ema_fast_period = self._config["ema_fast_period"]
        self.ema_slow_period = self._config["ema_slow_period"]
        self.ema_trend_period = self._config["ema_trend_period"]
        self.volume_sma_period = self._config["volume_sma_period"]

        # Декларируем наши потребности
        self.required_indicators = [
            {"name": "ema", "params": {"period": self.ema_fast_period}},
            {"name": "ema", "params": {"period": self.ema_slow_period}},
            {"name": "ema", "params": {"period": self.ema_trend_period}},
            {"name": "sma", "params": {"period": self.volume_sma_period, "column": "volume"}},
        ]

        # Имена колонок теперь соответствуют генерации в FeatureEngine
        self.ema_fast_name = f'EMA_{self.ema_fast_period}'
        self.ema_slow_name = f'EMA_{self.ema_slow_period}'
        self.ema_trend_name = f'EMA_{self.ema_trend_period}'
        self.volume_sma_name = f'SMA_{self.volume_sma_period}_volume'

        self.data_history = []

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Метод теперь пустой. Все расчеты делегированы FeatureEngine.
        Он может быть использован в будущем для уникальных, сложных расчетов.
        """
        return data

    def calculate_signals(self, event: MarketEvent):
        """
        Анализирует последнюю свечу и генерирует сигнал BUY или SELL,
        если все три "фильтра" стратегии совпадают.
        Логика здесь не изменилась, только имена колонок.
        """
        self.data_history.append(event.data)
        if len(self.data_history) > 2:
            self.data_history.pop(0)

        if len(self.data_history) < 2:
            return

        last_candle = self.data_history[-1]
        prev_candle = self.data_history[-2]

        # --- Условия для сигнала на ПОКУПКУ ---
        buy_trend = last_candle['close'] > last_candle[self.ema_trend_name]
        buy_impulse = prev_candle[self.ema_fast_name] < prev_candle[self.ema_slow_name] and \
                      last_candle[self.ema_fast_name] > last_candle[self.ema_slow_name]
        buy_volume = last_candle['volume'] > last_candle[self.volume_sma_name]

        if buy_trend and buy_impulse and buy_volume:
            signal = SignalEvent(timestamp=event.timestamp, instrument=self.instrument, direction="BUY", strategy_id=self.name)
            self.events_queue.put(signal)
            return

        # --- Условия для сигнала на ПРОДАЖУ (закрытие лонга) ---
        sell_trend = last_candle['close'] < last_candle[self.ema_trend_name]
        sell_impulse = prev_candle[self.ema_fast_name] > prev_candle[self.ema_slow_name] and \
                       last_candle[self.ema_fast_name] < last_candle[self.ema_slow_name]
        sell_volume = last_candle['volume'] > last_candle[self.volume_sma_name]

        if sell_trend and sell_impulse and sell_volume:
            signal = SignalEvent(timestamp=event.timestamp, instrument=self.instrument, direction="SELL", strategy_id=self.name)
            self.events_queue.put(signal)
            return