import pandas as pd
import pandas_ta as ta
from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy

class TripleFilterStrategy(BaseStrategy):
    """
    Реализация интрадей-стратегии "Тройной Фильтр" Александра Элдера.
    1. Тренд (EMA 200)
    2. Импульс (пересечение EMA 9 и EMA 21)
    3. Подтверждение объемом (Volume > SMA 20)
    """
    def __init__(self, events_queue: Queue, figi: str):
        super().__init__(events_queue, figi)
        self.name = "TripleFilter"
        
        # --- Реализация контракта из BaseStrategy ---
        self.candle_interval = "5min"
        self.stop_loss_percent = 0.7   # Убыток 0.7%
        self.take_profit_percent = 1.4 # Прибыль 1.4%
        
        # Храним только 2 последние свечи для анализа
        self.data_history = []

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Рассчитывает и добавляет в DataFrame индикаторы,
        необходимые для работы этой конкретной стратегии.
        """
        logging.info(f"Стратегия '{self.name}' подготавливает данные (расчет EMA, SMA)...")
        try:
            data.ta.ema(length=9, append=True, col_names=('EMA_9',))
            data.ta.ema(length=21, append=True, col_names=('EMA_21',))
            data.ta.ema(length=200, append=True, col_names=('EMA_200',))
            data.ta.sma(length=20, close='volume', append=True, col_names=('Volume_SMA_20',))
            
            # Удаляем строки с NaN, которые образуются в начале из-за расчета EMA
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
        buy_trend = last_candle['close'] > last_candle['EMA_200']
        buy_impulse = prev_candle['EMA_9'] < prev_candle['EMA_21'] and last_candle['EMA_9'] > last_candle['EMA_21']
        buy_volume = last_candle['volume'] > last_candle['Volume_SMA_20']
        
        if buy_trend and buy_impulse and buy_volume:
            signal = SignalEvent(figi=self.figi, direction="BUY", strategy_id=self.name)
            self.events_queue.put(signal)
            return

        # --- Условия для сигнала на ПРОДАЖУ (закрытие лонга) ---
        sell_trend = last_candle['close'] < last_candle['EMA_200']
        sell_impulse = prev_candle['EMA_9'] > prev_candle['EMA_21'] and last_candle['EMA_9'] < last_candle['EMA_21']
        sell_volume = last_candle['volume'] > last_candle['Volume_SMA_20']
        
        if sell_trend and sell_impulse and sell_volume:
            signal = SignalEvent(figi=self.figi, direction="SELL", strategy_id=self.name)
            self.events_queue.put(signal)
            return