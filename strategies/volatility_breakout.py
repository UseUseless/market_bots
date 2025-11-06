import pandas as pd
import pandas_ta as ta
from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy
from config import STRATEGY_CONFIG


class VolatilityBreakoutStrategy(BaseStrategy):
    """
    Стратегия пробоя волатильности с тремя режимами входа:
    1. Агрессивный (вход на пробое).
    2. Сбалансированный (вход после подтверждения).
    3. Консервативный (вход на откате после подтверждения).
    """

    _config = STRATEGY_CONFIG["VolatilityBreakoutStrategy"]
    candle_interval: str = _config["candle_interval"]

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)

        # --- Загрузка конфигурации ---
        self.variant = self._config["variant"]
        self.params = self._config[f"{self.variant}_params"]
        self.entry_params = self._config["entry_logic"]
        self.confirm_breakout = self.entry_params["confirm_breakout"]
        self.wait_for_pullback = self.entry_params["wait_for_pullback"]
        self.pullback_ema_period = self.entry_params.get("pullback_ema_period", 8)
        self.pullback_timeout_bars = self.entry_params.get("pullback_timeout_bars", 5)

        # Логирование режима работы
        entry_mode = "Агрессивный"
        if self.confirm_breakout and self.wait_for_pullback:
            entry_mode = "Консервативный (с откатом)"
        elif self.confirm_breakout:
            entry_mode = "Сбалансированный (с подтверждением)"
        logging.info(f"Стратегия '{self.name}' ({self.variant}) инициализирована. Режим входа: {entry_mode}")

        # --- Определение необходимой истории ---
        self.min_history_needed = 0
        if self.variant == "ClassicSqueeze":
            self.min_history_needed = max(self.params["bb_len"], self.params["kc_len"], self.params["trend_ema_period"])
        elif self.variant == "ADX_Donchian":
            self.min_history_needed = max(self.params["squeeze_period"], self.params["donchian_len"],
                                          self.params["adx_len"])
        if self.wait_for_pullback:
            self.min_history_needed = max(self.min_history_needed, self.pullback_ema_period)
        self.min_history_needed += 1

        # --- Машина состояний ---
        self.state = {
            "squeeze_was_on": False,
            "breakout_direction": None,
            "waiting_for_confirmation": False,
            "waiting_for_pullback": False,
            "pullback_bar_counter": 0
        }

    def _reset_state(self):
        """Сбрасывает машину состояний в исходное положение."""
        self.state["breakout_direction"] = None
        self.state["waiting_for_confirmation"] = False
        self.state["waiting_for_pullback"] = False
        self.state["pullback_bar_counter"] = 0

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Рассчитывает все необходимые индикаторы."""
        logging.debug(f"[{self.variant}] Расчет индикаторов...")

        if self.variant == "ClassicSqueeze":
            # ... (логика ClassicSqueeze)
            pass  # Оставим для полноты, но сейчас фокусируемся на ADX_Donchian

        elif self.variant == "ADX_Donchian":
            bbands_df = data.ta.bbands(length=self.params["bb_len"], std=self.params["bb_std"])
            bb_lower_col, bb_mid_col, bb_upper_col = bbands_df.columns[0], bbands_df.columns[1], bbands_df.columns[2]
            bband_width = (bbands_df[bb_upper_col] - bbands_df[bb_lower_col]) / bbands_df[bb_mid_col]
            quantile_threshold = bband_width.rolling(self.params['squeeze_period']).quantile(
                self.params['squeeze_quantile'])
            data['squeeze_on'] = bband_width < quantile_threshold

            data.ta.donchian(lower_length=self.params["donchian_len"], upper_length=self.params["donchian_len"],
                             append=True)
            data.ta.adx(length=self.params["adx_len"], append=True)

        # Добавляем EMA для отката, если опция включена
        if self.wait_for_pullback:
            data.ta.ema(length=self.pullback_ema_period, append=True)

        data.dropna(inplace=True)
        data.reset_index(drop=True, inplace=True)
        return data

    def _check_breakout_conditions(self, candle: pd.Series) -> str | None:
        """Проверяет условия пробоя и возвращает направление ('BUY'/'SELL') или None."""
        if self.variant == "ADX_Donchian":
            donchian_upper = candle[f'DCU_{self.params["donchian_len"]}_{self.params["donchian_len"]}']
            donchian_lower = candle[f'DCL_{self.params["donchian_len"]}_{self.params["donchian_len"]}']
            adx = candle[f'ADX_{self.params["adx_len"]}']
            if candle['close'] >= donchian_upper and adx > self.params["adx_threshold"]: return "BUY"
            if candle['close'] <= donchian_lower and adx > self.params["adx_threshold"]: return "SELL"
        return None

    def calculate_signals(self, event: MarketEvent):
        """Анализирует рыночные данные и управляет состояниями для генерации сигналов."""
        candle = event.data

        # --- Состояние 1: Ожидание отката ---
        if self.state["waiting_for_pullback"]:
            self.state["pullback_bar_counter"] += 1
            direction = self.state["breakout_direction"]
            pullback_ema = candle[f'EMA_{self.pullback_ema_period}']

            # Проверка таймаута
            if self.state["pullback_bar_counter"] > self.pullback_timeout_bars:
                logging.info(f"Откат не произошел в течение {self.pullback_timeout_bars} свечей. Сигнал отменен.")
                self._reset_state()
                return

            # Проверка касания EMA
            pullback_triggered = False
            if direction == "BUY" and candle['low'] <= pullback_ema:
                pullback_triggered = True
            elif direction == "SELL" and candle['high'] >= pullback_ema:
                pullback_triggered = True

            if pullback_triggered:
                logging.info(f"Откат к EMA({self.pullback_ema_period}) произошел. Генерирую сигнал {direction}.")
                self.events_queue.put(SignalEvent(self.instrument, direction, self.name))
                self._reset_state()
            return

        # --- Состояние 2: Ожидание подтверждения ---
        if self.state["waiting_for_confirmation"]:
            direction = self.state["breakout_direction"]
            confirmed = False

            if self.variant == "ADX_Donchian":
                donchian_upper = candle[f'DCU_{self.params["donchian_len"]}_{self.params["donchian_len"]}']
                donchian_lower = candle[f'DCL_{self.params["donchian_len"]}_{self.params["donchian_len"]}']
                if direction == "BUY" and candle['close'] >= donchian_upper: confirmed = True
                if direction == "SELL" and candle['close'] <= donchian_lower: confirmed = True

            if confirmed:
                logging.info("Пробой ПОДТВЕРЖДЕН.")
                if self.wait_for_pullback:
                    logging.info("Перехожу в режим ожидания отката.")
                    self.state["waiting_for_confirmation"] = False
                    self.state["waiting_for_pullback"] = True
                    self.state["pullback_bar_counter"] = 0
                else:
                    logging.info("Генерирую сигнал НЕМЕДЛЕННО после подтверждения.")
                    self.events_queue.put(SignalEvent(self.instrument, direction, self.name))
                    self._reset_state()
            else:
                logging.info("Пробой НЕ подтвержден, сигнал отменен.")
                self._reset_state()
            return

        # --- Состояние 0: Поиск "выстрела" сжатия ---
        if candle['squeeze_on']:
            self.state["squeeze_was_on"] = True
            return

        if self.state["squeeze_was_on"] and not candle['squeeze_on']:
            logging.info(f"[SQUEEZE FIRED] Обнаружен выход из сжатия на свече {candle['time']}")
            self.state["squeeze_was_on"] = False  # Сбрасываем, чтобы не срабатывать на каждой след. свече

            direction = self._check_breakout_conditions(candle)
            if direction:
                if self.confirm_breakout:
                    logging.info(f"Обнаружен пробой {direction}. Ожидаю подтверждения на следующей свече.")
                    self.state["waiting_for_confirmation"] = True
                    self.state["breakout_direction"] = direction
                else:  # Агрессивный вход без подтверждения и без отката
                    logging.info(f"Обнаружен пробой {direction}. Генерирую сигнал НЕМЕДЛЕННО.")
                    self.events_queue.put(SignalEvent(self.instrument, direction, self.name))
                    self._reset_state()  # Сразу сбрасываем состояние