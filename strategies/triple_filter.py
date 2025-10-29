import pandas as pd
import pandas_ta as ta
from queue import Queue
import logging

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

    def __init__(self, events_queue: Queue, figi: str):
        super().__init__(events_queue, figi)

        # Периоды
        self.ema_fast_period = self._config["ema_fast_period"]
        self.ema_slow_period = self._config["ema_slow_period"]
        self.ema_trend_period = self._config["ema_trend_period"]
        self.volume_sma_period = self._config["volume_sma_period"]

        # Имена колонок
        self.ema_fast_name = f'EMA_{self.ema_fast_period}'
        self.ema_slow_name = f'EMA_{self.ema_slow_period}'
        self.ema_trend_name = f'EMA_{self.ema_trend_period}'
        self.volume_sma_name = f'Volume_SMA_{self.volume_sma_period}'

        # Внутреннее состояние стратегии. Храним только 2 последние свечи для анализа
        self.data_history = []

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Рассчитывает и добавляет в DataFrame индикаторы,
        необходимые для работы этой конкретной стратегии.
        """
        logging.info(f"Стратегия '{self.name}' подготавливает данные (расчет EMA, SMA)...")
        try:
            data.ta.ema(length=self.ema_fast_period, append=True, col_names=(self.ema_fast_name,))
            data.ta.ema(length=self.ema_slow_period, append=True, col_names=(self.ema_slow_name,))
            data.ta.ema(length=self.ema_trend_period, append=True, col_names=(self.ema_trend_name,))
            data.ta.sma(length=self.volume_sma_period, close='volume', append=True, col_names=(self.volume_sma_name,))
            
            # Удаляем строки с NaN, которые образуются в начале из-за расчета EMA
            # И именно после всех расчетов
            data.dropna(inplace=True)
            data.reset_index(drop=True, inplace=True)
            logging.info("Подготовка данных для TripleFilterStrategy завершена.")
        except Exception as e:
            logging.error(f"Ошибка при расчете индикаторов в TripleFilterStrategy: {e}")
            # Возвращаем пустой DataFrame в случае ошибки
            return pd.DataFrame()
            
        return data

    def calculate_signals(self, event: MarketEvent):
        """
        Анализирует последнюю свечу и генерирует сигнал BUY или SELL,
        если все три "фильтра" стратегии совпадают.
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
            signal = SignalEvent(figi=self.figi, direction="BUY", strategy_id=self.name)
            self.events_queue.put(signal)
            return

        # --- Условия для сигнала на ПРОДАЖУ (закрытие лонга) ---
        sell_trend = last_candle['close'] < last_candle[self.ema_trend_name]
        sell_impulse = prev_candle[self.ema_fast_name] > prev_candle[self.ema_slow_name] and \
                       last_candle[self.ema_fast_name] < last_candle[self.ema_slow_name]
        sell_volume = last_candle['volume'] > last_candle[self.volume_sma_name]
        
        if sell_trend and sell_impulse and sell_volume:
            signal = SignalEvent(figi=self.figi, direction="SELL", strategy_id=self.name)
            self.events_queue.put(signal)
            return