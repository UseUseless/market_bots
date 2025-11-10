import pandas as pd
from queue import Queue
from typing import Dict, Any, Optional

from core.event import SignalEvent
from strategies.base_strategy import BaseStrategy


class TripleFilterStrategy(BaseStrategy):
    """
    Реализация интрадей-стратегии "Тройной Фильтр" Александра Элдера.
    """
    params_config = {
        "candle_interval": {
            "type": "str",
            "default": "5min",
            "optimizable": False,
            "description": "Рекомендуемый таймфрейм."
        },
        "ema_fast_period": {
            "type": "int",
            "default": 9,
            "optimizable": True,
            "low": 5,
            "high": 20,
            "step": 1,
            "description": "Период быстрой EMA (импульс)."
        },
        "ema_slow_period": {
            "type": "int",
            "default": 21,
            "optimizable": True,
            "low": 21,
            "high": 50,
            "step": 1,
            "description": "Период медленной EMA (импульс)."
        },
        "ema_trend_period": {
            "type": "int",
            "default": 200,
            "optimizable": False, # Обычно не оптимизируют
            "description": "Период трендовой EMA."
        },
        "volume_sma_period": {
            "type": "int",
            "default": 20,
            "optimizable": False, # Обычно не оптимизируют
            "description": "Период SMA для фильтра объема."
        }
    }

    def __init__(self, events_queue: Queue, instrument: str, params: Dict[str, Any],
                 risk_manager_type: str, risk_manager_params: Optional[Dict[str, Any]] = None):

        # 1. Извлекаем параметры из переданного словаря `params`
        self.ema_fast_period = params["ema_fast_period"]
        self.ema_slow_period = params["ema_slow_period"]
        self.ema_trend_period = params["ema_trend_period"]
        self.volume_sma_period = params["volume_sma_period"]

        # 2. Динамически формируем зависимости на основе параметров
        self.min_history_needed = self.ema_trend_period + 1
        self.required_indicators = [
            {"name": "ema", "params": {"period": self.ema_fast_period}},
            {"name": "ema", "params": {"period": self.ema_slow_period}},
            {"name": "ema", "params": {"period": self.ema_trend_period}},
            {"name": "sma", "params": {"period": self.volume_sma_period, "column": "volume"}},
        ]

        # 3. Вызываем родительский __init__ ПОСЛЕ определения зависимостей
        super().__init__(events_queue, instrument, params, risk_manager_type, risk_manager_params)

        # 4. Определяем имена колонок для удобства
        self.ema_fast_name = f'EMA_{self.ema_fast_period}'
        self.ema_slow_name = f'EMA_{self.ema_slow_period}'
        self.ema_trend_name = f'EMA_{self.ema_trend_period}'
        self.volume_sma_name = f'SMA_{self.volume_sma_period}_volume'

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Анализирует последнюю свечу и генерирует сигнал BUY или SELL,
        если все три "фильтра" стратегии совпадают.
        """
        # --- Условия для сигнала на ПОКУПКУ ---
        buy_trend = last_candle['close'] > last_candle[self.ema_trend_name]
        buy_impulse = prev_candle[self.ema_fast_name] < prev_candle[self.ema_slow_name] and \
                      last_candle[self.ema_fast_name] > last_candle[self.ema_slow_name]
        buy_volume = last_candle['volume'] > last_candle[self.volume_sma_name]

        if buy_trend and buy_impulse and buy_volume:
            signal = SignalEvent(timestamp=timestamp, instrument=self.instrument, direction="BUY",
                                 strategy_id=self.name)
            self.events_queue.put(signal)
            return

        # --- Условия для сигнала на ПРОДАЖУ (закрытие лонга или вход в шорт) ---
        sell_trend = last_candle['close'] < last_candle[self.ema_trend_name]
        sell_impulse = prev_candle[self.ema_fast_name] > prev_candle[self.ema_slow_name] and \
                       last_candle[self.ema_fast_name] < last_candle[self.ema_slow_name]
        sell_volume = last_candle['volume'] > last_candle[self.volume_sma_name]

        if sell_trend and sell_impulse and sell_volume:
            signal = SignalEvent(timestamp=timestamp, instrument=self.instrument, direction="SELL",
                                 strategy_id=self.name)
            self.events_queue.put(signal)
            return