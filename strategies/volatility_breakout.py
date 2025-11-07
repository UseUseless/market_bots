import pandas as pd
from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy
from config import STRATEGY_CONFIG

logger = logging.getLogger('backtester')

class VolatilityBreakoutStrategy(BaseStrategy):
    _config = STRATEGY_CONFIG["VolatilityBreakoutStrategy"]
    candle_interval: str = _config["candle_interval"]

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)

        self.variant = self._config["variant"]
        self.params = self._config[f"{self.variant}_params"]
        self.entry_params = self._config["entry_logic"]

        self.breakout_timeout_bars = self.entry_params.get("breakout_timeout_bars", 3)
        self.confirm_breakout = self.entry_params.get("confirm_breakout", False)
        self.wait_for_pullback = self.entry_params.get("wait_for_pullback", False)
        self.pullback_ema_period = self.entry_params.get("pullback_ema_period", 8)
        self.pullback_timeout_bars = self.entry_params.get("pullback_timeout_bars", 5)

        entry_mode = "Агрессивный"
        if self.confirm_breakout and self.wait_for_pullback:
            entry_mode = "Консервативный (с откатом)"
        elif self.confirm_breakout:
            entry_mode = "Сбалансированный (с подтверждением)"
        logger.info(f"Стратегия '{self.name}' ({self.variant}) инициализирована. Режим входа: {entry_mode}")

        self.required_indicators = []
        if self.variant == "ADX_Donchian":
            self.required_indicators.extend([
                {"name": "bbands", "params": {"period": self.params["bb_len"], "std": self.params["bb_std"]}},
                {"name": "donchian",
                 "params": {"lower_period": self.params["donchian_len"], "upper_period": self.params["donchian_len"]}},
                {"name": "adx", "params": {"period": self.params["adx_len"]}},
            ])
        if self.wait_for_pullback:
            self.required_indicators.append({"name": "ema", "params": {"period": self.pullback_ema_period}})

        self.min_history_needed = 0
        if self.variant == "ADX_Donchian":
            self.min_history_needed = max(self.params["squeeze_period"], self.params["donchian_len"],
                                          self.params["adx_len"])
        if self.wait_for_pullback:
            self.min_history_needed = max(self.min_history_needed, self.pullback_ema_period)
        self.min_history_needed += 1

        self.state = {
            "squeeze_was_on": False,
            "waiting_for_breakout": False,
            "breakout_bar_counter": 0,
            "breakout_direction": None,
            "waiting_for_confirmation": False,
            "waiting_for_pullback": False,
            "pullback_bar_counter": 0
        }

        self.data_history = []

    def _reset_state(self):
        self.state.update({
            "squeeze_was_on": False,
            "waiting_for_breakout": False,
            "breakout_bar_counter": 0,
            "breakout_direction": None,
            "waiting_for_confirmation": False,
            "waiting_for_pullback": False,
            "pullback_bar_counter": 0
        })

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        if self.variant == "ADX_Donchian":
            logger.info(f"Стратегия '{self.name}' рассчитывает кастомный 'squeeze_on'...")

            std_str = str(self.params["bb_std"]).replace('.', '_')
            bb_upper_col = f'BBU_{self.params["bb_len"]}_{std_str}'
            bb_lower_col = f'BBL_{self.params["bb_len"]}_{std_str}'
            bb_mid_col = f'BBM_{self.params["bb_len"]}_{std_str}'

            if not all(col in data.columns for col in [bb_upper_col, bb_lower_col, bb_mid_col]):
                logger.error("Не найдены колонки Bollinger Bands. Расчет 'squeeze_on' невозможен.")
                return pd.DataFrame()

            bband_width = (data[bb_upper_col] - data[bb_lower_col]) / data[bb_mid_col]
            quantile_threshold = bband_width.rolling(self.params['squeeze_period']).quantile(
                self.params['squeeze_quantile'])
            data['squeeze_on'] = bband_width < quantile_threshold

        return data

    def _check_breakout_conditions(self, current_candle: pd.Series, prev_candle: pd.Series) -> str | None:
        if self.variant == "ADX_Donchian":
            donchian_len = self.params["donchian_len"]
            # Берем значения канала с ПРЕДЫДУЩЕЙ свечи
            donchian_upper = prev_candle[f'DCU_{donchian_len}_{donchian_len}']
            donchian_lower = prev_candle[f'DCL_{donchian_len}_{donchian_len}']
            # А ADX для силы тренда - с ТЕКУЩЕЙ
            adx = current_candle[f'ADX_{self.params["adx_len"]}']

            # Сравниваем ТЕКУЩЕЕ закрытие с ПРЕДЫДУЩИМ барьером
            if current_candle['close'] > donchian_upper and adx > self.params["adx_threshold"]: return "BUY"
            if current_candle['close'] < donchian_lower and adx > self.params["adx_threshold"]: return "SELL"
        return None

    def calculate_signals(self, event: MarketEvent):
        self.data_history.append(event.data)
        if len(self.data_history) > 2:
            self.data_history.pop(0)

        if len(self.data_history) < 2:
            return

        last_candle = self.data_history[-1]
        prev_candle = self.data_history[-2]

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
                self.events_queue.put(SignalEvent(event.timestamp, self.instrument, direction, self.name))
                self._reset_state()
            return

        # --- Состояние 2: Ожидание подтверждения ---
        if self.state["waiting_for_confirmation"]:
            direction = self.state["breakout_direction"]
            donchian_len = self.params["donchian_len"]
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
                    self.events_queue.put(SignalEvent(event.timestamp, self.instrument, direction, self.name))
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
                    self.events_queue.put(SignalEvent(event.timestamp, self.instrument, direction, self.name))
                    self._reset_state()
            return

        # --- Состояние 4: Поиск начала сжатия и "выстрела" ---
        if 'squeeze_on' not in last_candle: return

        if last_candle['squeeze_on']:
            self.state["squeeze_was_on"] = True
            return

        if self.state["squeeze_was_on"] and not last_candle['squeeze_on']:
            logger.info(f"[SQUEEZE FIRED] Обнаружен выход из сжатия на свече {last_candle['time']}")
            self.state["squeeze_was_on"] = False
            self.state["waiting_for_breakout"] = True
            self.state["breakout_bar_counter"] = 0
            logger.info(f"Перехожу в режим ожидания пробоя на {self.breakout_timeout_bars} свечей.")