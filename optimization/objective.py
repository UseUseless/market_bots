import optuna
import pandas as pd
import numpy as np
from copy import deepcopy
from typing import Type

from core.backtest_engine import run_backtest_session
from config import BACKTEST_CONFIG, STRATEGY_CONFIG, RISK_CONFIG
from strategies.base_strategy import BaseStrategy
from optimization.search_space import SEARCH_SPACE


def _calculate_calmar_ratio(trades_df: pd.DataFrame, initial_capital: float) -> float:
    """Рассчитывает Calmar Ratio."""

    if trades_df.empty or len(trades_df) < 2:
        # Calmar Ratio не имеет смысла, если сделок меньше двух.
        return 0.0

        # --- Расчет максимальной просадки (Max Drawdown) ---
    trades_df['cumulative_pnl'] = trades_df['pnl'].cumsum()
    trades_df['equity_curve'] = initial_capital + trades_df['cumulative_pnl']
    high_water_mark = trades_df['equity_curve'].cummax()
    drawdown = (trades_df['equity_curve'] - high_water_mark) / high_water_mark
    max_drawdown = abs(drawdown.min())

    if max_drawdown == 0:
        # Если просадки не было, возвращаем очень большое число (inf), если была прибыль, иначе 0.
        return np.inf if trades_df['pnl'].sum() > 0 else 0.0

    # --- Расчет годовой доходности (Annualized Return) ---
    total_pnl = trades_df['cumulative_pnl'].iloc[-1]
    total_return = total_pnl / initial_capital

    # Рассчитываем продолжительность торгового периода в днях
    start_date = pd.to_datetime(trades_df['entry_timestamp_utc'].iloc[0])
    end_date = pd.to_datetime(trades_df['exit_timestamp_utc'].iloc[-1])
    num_days = (end_date - start_date).days

    # Избегаем деления на ноль и бессмысленных расчетов, если период слишком короткий
    if num_days < 1:
        num_days = 1

    # Формула для расчета среднегодовой доходности
    annualized_return = ((1 + total_return) ** (365.0 / num_days)) - 1

    return annualized_return / max_drawdown


class Objective:
    """Класс-обертка для целевой функции, чтобы передавать статичные параметры."""

    def __init__(self, strategy_class: Type[BaseStrategy], exchange: str, instrument: str, interval: str, risk_manager_type: str, data_slice: pd.DataFrame):
        self.strategy_class = strategy_class
        self.exchange = exchange
        self.instrument = instrument
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.data_slice = data_slice
        self.strategy_key = self.strategy_class.__name__

    def __call__(self, trial: optuna.Trial) -> float:
        """Одна итерация оптимизации."""
        try:
            # --- 1. Создаем временные копии конфигов ---
            strategy_config_override = deepcopy(STRATEGY_CONFIG)
            risk_config_override = deepcopy(RISK_CONFIG)

            # --- 2. Получаем параметры от Optuna и обновляем конфиги ---

            # Параметры стратегии
            strategy_search_space = SEARCH_SPACE["strategy_params"].get(self.strategy_key, {})
            for param, settings in strategy_search_space.items():
                method = getattr(trial, settings["method"])
                value = method(**settings["kwargs"])
                # Используем правильный ключ (имя класса)
                strategy_config_override[self.strategy_key][param] = value

            rm_search_space = SEARCH_SPACE["risk_manager_params"].get(self.risk_manager_type, {})
            for param, settings in rm_search_space.items():
                method = getattr(trial, settings["method"])
                value = method(**settings["kwargs"])
                risk_config_override[param] = value

            backtest_settings = {
                "strategy_class": self.strategy_class,  # Передаем сам класс
                "exchange": self.exchange,
                "instrument": self.instrument,
                "interval": self.interval,
                "risk_manager_type": self.risk_manager_type,
                "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
                "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
                "data_dir": "data",
                "trade_log_path": None,
                "strategy_config": strategy_config_override,
                "risk_config": risk_config_override,
                "data_slice": self.data_slice
            }

            # --- 4. Запускаем бэктест ---
            backtest_results  = run_backtest_session(backtest_settings)

            # --- 5. Анализируем результат и возвращаем метрику ---
            if backtest_results ["status"] != "success" or backtest_results ["trades_df"].empty:
                # Если бэктест упал или не было сделок, "наказываем" эту комбинацию параметров.
                # Pruning - механизм Optuna для досрочного отсечения бесперспективных веток.
                raise optuna.TrialPruned()

            trades_df = backtest_results["trades_df"].copy()
            trades_df['entry_timestamp_utc'] = pd.to_datetime(trades_df['entry_timestamp_utc'])
            trades_df['exit_timestamp_utc'] = pd.to_datetime(trades_df['exit_timestamp_utc'])

            calmar_ratio = _calculate_calmar_ratio(trades_df, backtest_results["initial_capital"])
            return calmar_ratio

        except optuna.TrialPruned:
            raise
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Ошибка в trial #{trial.number}: {e}", exc_info=True)
            return -1.0