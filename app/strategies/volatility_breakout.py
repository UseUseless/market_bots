import pandas as pd
from queue import Queue
import logging
from typing import Dict, Any, Optional

from app.core.models.event import SignalEvent
from app.strategies.base_strategy import BaseStrategy
from app.core.services.feature_engine import FeatureEngine

logger = logging.getLogger('backtester')

class VolatilityBreakoutStrategy(BaseStrategy):
    """
    Стратегия пробоя волатильности, основанная на концепции "сжатия" (Squeeze).
    """

    params_config = {
        "candle_interval": {"type": "str", "default": "1hour", "optimizable": False},
        "variant": {"type": "str", "default": "ADX_Donchian", "optimizable": False},  # Вариант не оптимизируем

        # Группа entry_logic
        "entry_breakout_timeout_bars": {"type": "int", "default": 3, "optimizable": True, "low": 2, "high": 7},
        "entry_confirm_breakout": {"type": "bool", "default": False, "optimizable": False},
        # Булевы пока не оптимизируем
        "entry_wait_for_pullback": {"type": "bool", "default": False, "optimizable": False},
        "entry_pullback_ema_period": {"type": "int", "default": 8, "optimizable": True, "low": 5, "high": 13},
        "entry_pullback_timeout_bars": {"type": "int", "default": 5, "optimizable": True, "low": 3, "high": 8},

        # Группа ADX_Donchian_params
        "adx_bb_len": {"type": "int", "default": 20, "optimizable": True, "low": 15, "high": 30},
        "adx_bb_std": {"type": "float", "default": 2.0, "optimizable": True, "low": 1.5, "high": 2.5, "step": 0.1},
        "adx_squeeze_period": {"type": "int", "default": 50, "optimizable": True, "low": 30, "high": 70},
        "adx_squeeze_quantile": {"type": "float", "default": 0.05, "optimizable": True, "low": 0.01, "high": 0.15,
                                 "step": 0.01},
        "adx_donchian_len": {"type": "int", "default": 20, "optimizable": True, "low": 15, "high": 30},
        "adx_adx_len": {"type": "int", "default": 14, "optimizable": True, "low": 10, "high": 20},
        "adx_adx_threshold": {"type": "int", "default": 20, "optimizable": True, "low": 18, "high": 30},
    }

    def __init__(self, events_queue: Queue, instrument: str, params: Dict[str, Any],
                 feature_engine: FeatureEngine, risk_manager_type: str, risk_manager_params: Optional[Dict[str, Any]] = None):

        # 1. Извлекаем параметры из `params`
        self.variant = params["variant"]
        self.breakout_timeout_bars = params["entry_breakout_timeout_bars"]
        self.confirm_breakout = params["entry_confirm_breakout"]
        self.wait_for_pullback = params["entry_wait_for_pullback"]
        self.pullback_ema_period = params["entry_pullback_ema_period"]
        self.pullback_timeout_bars = params["entry_pullback_timeout_bars"]

        # 2. Динамически формируем зависимости
        self.required_indicators = []
        if self.variant == "ADX_Donchian":
            self.required_indicators.extend([
                {"name": "bbands", "params": {"period": params["adx_bb_len"], "std": params["adx_bb_std"]}},
                {"name": "donchian",
                 "params": {"lower_period": params["adx_donchian_len"], "upper_period": params["adx_donchian_len"]}},
                {"name": "adx", "params": {"period": params["adx_adx_len"]}},
            ])
        if self.wait_for_pullback:
            self.required_indicators.append({"name": "ema", "params": {"period": self.pullback_ema_period}})

        self.min_history_needed = 0
        if self.variant == "ADX_Donchian":
            self.min_history_needed = max(
                params["adx_squeeze_period"], params["adx_donchian_len"], params["adx_adx_len"]
            )
        if self.wait_for_pullback:
            self.min_history_needed = max(self.min_history_needed, self.pullback_ema_period)
        self.min_history_needed += 1

        # 3. Вызываем родительский __init__
        super().__init__(events_queue, instrument, params,
                         feature_engine, risk_manager_type, risk_manager_params)

        # 4. Инициализация состояний
        self.state = {
            "squeeze_was_on": False, "waiting_for_breakout": False, "breakout_bar_counter": 0,
            "breakout_direction": None, "waiting_for_confirmation": False,
            "waiting_for_pullback": False, "pullback_bar_counter": 0
        }

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        if self.variant == "ADX_Donchian":
            # Используем параметры из self.params
            std_str = str(self.params["adx_bb_std"]).replace('.', '_')
            bb_len = self.params["adx_bb_len"]

            # Формируем имена колонок, которые должен был создать FeatureEngine
            bb_upper_col = f'BBU_{bb_len}_{std_str}'
            bb_lower_col = f'BBL_{bb_len}_{std_str}'
            bb_mid_col = f'BBM_{bb_len}_{std_str}'

            if not all(col in data.columns for col in [bb_upper_col, bb_lower_col, bb_mid_col]):
                logger.warning("Не найдены колонки Bollinger Bands. Пропуск расчета squeeze.")
                return data

            bband_width = (data[bb_upper_col] - data[bb_lower_col]) / data[bb_mid_col]

            quantile_threshold = bband_width.rolling(self.params['adx_squeeze_period']).quantile(
                self.params['adx_squeeze_quantile'])
            data['squeeze_on'] = bband_width < quantile_threshold
        return data

    def _reset_state(self):
        self.state.update({
            "squeeze_was_on": False, "waiting_for_breakout": False, "breakout_bar_counter": 0,
            "breakout_direction": None, "waiting_for_confirmation": False,
            "waiting_for_pullback": False, "pullback_bar_counter": 0
        })

    def _check_breakout_conditions(self, current_candle: pd.Series, prev_candle: pd.Series) -> str | None:
        if self.variant == "ADX_Donchian":
            donchian_len = self.params["adx_donchian_len"]
            adx_len = self.params["adx_adx_len"]
            adx_threshold = self.params["adx_adx_threshold"]

            donchian_upper = prev_candle[f'DCU_{donchian_len}_{donchian_len}']
            donchian_lower = prev_candle[f'DCL_{donchian_len}_{donchian_len}']
            adx = current_candle[f'ADX_{adx_len}']

            if current_candle['close'] > donchian_upper and adx > adx_threshold: return "BUY"
            if current_candle['close'] < donchian_lower and adx > adx_threshold: return "SELL"
        return None

    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        # --- Состояние 1: Ожидание отката ---
        if self.state["waiting_for_pullback"]:
            self.state["pullback_bar_counter"] += 1
            direction = self.state["breakout_direction"]
            pullback_ema = last_candle[f'EMA_{self.pullback_ema_period}']

            if self.state["pullback_bar_counter"] > self.pullback_timeout_bars:
                logger.info(f"Откат не произошел в течение {self.pullback_timeout_bars} свечей. Сигнал отменен.")
                self._reset_state()
                return

            pullback_triggered = (direction == "BUY" and last_candle['low'] <= pullback_ema) or \
                                 (direction == "SELL" and last_candle['high'] >= pullback_ema)

            if pullback_triggered:
                logger.info(f"Откат к EMA({self.pullback_ema_period}) произошел. Генерирую сигнал {direction}.")
                self.events_queue.put(SignalEvent(timestamp, self.instrument, direction, self.name))
                self._reset_state()
            return

        # --- Состояние 2: Ожидание подтверждения ---
        if self.state["waiting_for_confirmation"]:
            direction = self.state["breakout_direction"]
            donchian_len = self.params.get("donchian_len", 20)
            donchian_upper = prev_candle[f'DCU_{donchian_len}_{donchian_len}']
            donchian_lower = prev_candle[f'DCL_{donchian_len}_{donchian_len}']

            confirmed = (direction == "BUY" and last_candle['close'] > donchian_upper) or \
                        (direction == "SELL" and last_candle['close'] < donchian_lower)

            if confirmed:
                logger.info("Пробой ПОДТВЕРЖДЕН.")
                if self.wait_for_pullback:
                    logger.info("Перехожу в режим ожидания отката.")
                    self.state["waiting_for_confirmation"] = False
                    self.state["waiting_for_pullback"] = True
                    self.state["pullback_bar_counter"] = 0
                else:
                    logger.info("Генерирую сигнал НЕМЕДЛЕННО после подтверждения.")
                    self.events_queue.put(SignalEvent(timestamp, self.instrument, direction, self.name))
                    self._reset_state()
            else:
                logger.info("Пробой НЕ подтвержден, сигнал отменен.")
                self._reset_state()
            return

        # --- Состояние 3: Ожидание пробоя после "выстрела" ---
        if self.state["waiting_for_breakout"]:
            self.state["breakout_bar_counter"] += 1
            logger.debug(f"Ожидание пробоя, свеча #{self.state['breakout_bar_counter']}...")

            if self.state["breakout_bar_counter"] > self.breakout_timeout_bars:
                logger.info(f"Пробой не произошел в течение {self.breakout_timeout_bars} свечей. Сигнал отменен.")
                self._reset_state()
                return

            direction = self._check_breakout_conditions(last_candle, prev_candle)
            if direction:
                if self.confirm_breakout:
                    logger.info(f"Обнаружен пробой {direction}. Ожидаю подтверждения на следующей свече.")
                    self.state["waiting_for_confirmation"] = True
                    self.state["breakout_direction"] = direction
                    self.state["waiting_for_breakout"] = False
                else:
                    logger.info(f"Обнаружен пробой {direction}. Генерирую сигнал НЕМЕДЛЕННО.")
                    self.events_queue.put(SignalEvent(timestamp, self.instrument, direction, self.name))
                    self._reset_state()
            return

        # --- Состояние 4: Поиск начала сжатия и "выстрела" ---
        if last_candle['squeeze_on']:
            self.state["squeeze_was_on"] = True
            return

        if self.state["squeeze_was_on"] and not last_candle['squeeze_on']:
            logger.info(f"[SQUEEZE FIRED] Обнаружен выход из сжатия на свече {last_candle['time']}")
            self.state["squeeze_was_on"] = False
            self.state["waiting_for_breakout"] = True
            self.state["breakout_bar_counter"] = 0
            logger.info(f"Перехожу в режим ожидания пробоя на {self.breakout_timeout_bars} свечей.")