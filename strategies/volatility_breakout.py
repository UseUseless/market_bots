import pandas as pd
from queue import Queue
import logging
from typing import Dict, Any, Optional

from core.event import SignalEvent
from strategies.base_strategy import BaseStrategy

logger = logging.getLogger('backtester')

class VolatilityBreakoutStrategy(BaseStrategy):
    """
    Стратегия пробоя волатильности, основанная на концепции "сжатия" (Squeeze).
    """

    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None,
                 risk_manager_type: str = "FIXED", risk_config: Optional[Dict[str, Any]] = None):
        # --- Сначала определяем параметры и зависимости ---
        _strategy_config = strategy_config if strategy_config is not None else {}
        strategy_params = _strategy_config.get(self.__class__.__name__, {})

        self.variant = strategy_params.get("variant", "ADX_Donchian")
        self.params = strategy_params.get(f"{self.variant}_params", {})
        self.entry_params = strategy_params.get("entry_logic", {})

        self.breakout_timeout_bars = self.entry_params.get("breakout_timeout_bars", 3)
        self.confirm_breakout = self.entry_params.get("confirm_breakout", False)
        self.wait_for_pullback = self.entry_params.get("wait_for_pullback", False)
        self.pullback_ema_period = self.entry_params.get("pullback_ema_period", 8)
        self.pullback_timeout_bars = self.entry_params.get("pullback_timeout_bars", 5)

        self.required_indicators = []
        if self.variant == "ADX_Donchian":
            self.required_indicators.extend([
                {"name": "bbands",
                 "params": {"period": self.params.get("bb_len", 20), "std": self.params.get("bb_std", 2.0)}},
                {"name": "donchian", "params": {"lower_period": self.params.get("donchian_len", 20),
                                                "upper_period": self.params.get("donchian_len", 20)}},
                {"name": "adx", "params": {"period": self.params.get("adx_len", 14)}},
            ])
        if self.wait_for_pullback:
            self.required_indicators.append({"name": "ema", "params": {"period": self.pullback_ema_period}})

        self.min_history_needed = 0
        if self.variant == "ADX_Donchian":
            self.min_history_needed = max(
                self.params.get("squeeze_period", 50), self.params.get("donchian_len", 20),
                self.params.get("adx_len", 14)
            )
        if self.wait_for_pullback:
            self.min_history_needed = max(self.min_history_needed, self.pullback_ema_period)
        self.min_history_needed += 1

        # Теперь вызываем родительский конструктор
        super().__init__(events_queue, instrument, strategy_config, risk_manager_type, risk_config)

        # Инициализация состояний
        self.state = {
            "squeeze_was_on": False, "waiting_for_breakout": False, "breakout_bar_counter": 0,
            "breakout_direction": None, "waiting_for_confirmation": False,
            "waiting_for_pullback": False, "pullback_bar_counter": 0
        }

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Рассчитывает кастомный индикатор 'squeeze_on'.
        """
        if self.variant == "ADX_Donchian":
            logger.info(f"Стратегия '{self.name}' рассчитывает кастомный 'squeeze_on'...")
            std_str = str(self.params.get("bb_std", 2.0)).replace('.', '_')
            bb_len = self.params.get("bb_len", 20)
            bb_upper_col = f'BBU_{bb_len}_{std_str}'
            bb_lower_col = f'BBL_{bb_len}_{std_str}'
            bb_mid_col = f'BBM_{bb_len}_{std_str}'

            if not all(col in data.columns for col in [bb_upper_col, bb_lower_col, bb_mid_col]):
                logger.error("Не найдены колонки Bollinger Bands. Расчет 'squeeze_on' невозможен.")
                return pd.DataFrame()

            bband_width = (data[bb_upper_col] - data[bb_lower_col]) / data[bb_mid_col]
            quantile_threshold = bband_width.rolling(self.params.get('squeeze_period', 50)).quantile(
                self.params.get('squeeze_quantile', 0.05))
            data['squeeze_on'] = bband_width < quantile_threshold

            # Добавляем кастомную колонку в список для проверки
            self._required_cols.append('squeeze_on')
        return data

    def _reset_state(self):
        self.state.update({
            "squeeze_was_on": False, "waiting_for_breakout": False, "breakout_bar_counter": 0,
            "breakout_direction": None, "waiting_for_confirmation": False,
            "waiting_for_pullback": False, "pullback_bar_counter": 0
        })

    def _check_breakout_conditions(self, current_candle: pd.Series, prev_candle: pd.Series) -> str | None:
        if self.variant == "ADX_Donchian":
            donchian_len = self.params.get("donchian_len", 20)
            adx_len = self.params.get("adx_len", 14)
            adx_threshold = self.params.get("adx_threshold", 20)

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