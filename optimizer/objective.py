import optuna
import pandas as pd
import numpy as np
from copy import deepcopy

from core.backtest_engine import run_backtest_session
from config import BACKTEST_CONFIG, STRATEGY_CONFIG, RISK_CONFIG
from strategies import AVAILABLE_STRATEGIES
from optimizer.search_space import SEARCH_SPACE


def _calculate_calmar_ratio(trades_df: pd.DataFrame, initial_capital: float) -> float:
    """Рассчитывает Calmar Ratio."""
    if trades_df.empty:
        return 0.0

    trades_df['cumulative_pnl'] = trades_df['pnl'].cumsum()
    trades_df['equity_curve'] = initial_capital + trades_df['cumulative_pnl']

    high_water_mark = trades_df['equity_curve'].cummax()
    drawdown = (trades_df['equity_curve'] - high_water_mark) / high_water_mark
    max_drawdown = abs(drawdown.min())

    total_pnl = trades_df['equity_curve'].iloc[-1] - initial_capital
    annualized_return = total_pnl / initial_capital  # Упрощенно, без учета времени

    if max_drawdown == 0:
        return np.inf if annualized_return > 0 else 0.0

    return annualized_return / max_drawdown


class Objective:
    """Класс-обертка для целевой функции, чтобы передавать статичные параметры."""

    def __init__(self, strategy_name: str, exchange: str, instrument: str, interval: str, risk_manager_type: str):
        self.strategy_name = strategy_name
        self.exchange = exchange
        self.instrument = instrument
        self.interval = interval
        self.risk_manager_type = risk_manager_type

    def __call__(self, trial: optuna.Trial) -> float:
        """Одна итерация оптимизации."""
        try:
            # --- 1. Создаем временные копии конфигов ---
            strategy_config_override = deepcopy(STRATEGY_CONFIG)
            risk_config_override = deepcopy(RISK_CONFIG)

            # --- 2. Получаем параметры от Optuna и обновляем конфиги ---

            # Параметры стратегии
            strategy_search_space = SEARCH_SPACE["strategy_params"].get(self.strategy_name, {})
            for param, settings in strategy_search_space.items():
                method = getattr(trial, settings["method"])
                value = method(**settings["kwargs"])
                strategy_config_override[self.strategy_name][param] = value

            # Параметры риск-менеджера
            rm_search_space = SEARCH_SPACE["risk_manager_params"].get(self.risk_manager_type, {})
            for param, settings in rm_search_space.items():
                method = getattr(trial, settings["method"])
                value = method(**settings["kwargs"])
                risk_config_override[param] = value

            # --- 3. Собираем конфиг для движка бэктеста ---
            backtest_config = {
                "strategy_class": AVAILABLE_STRATEGIES[self.strategy_name],
                "exchange": self.exchange,
                "instrument": self.instrument,
                "interval": self.interval,
                "risk_manager_type": self.risk_manager_type,
                "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
                "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
                "data_dir": "data",
                "trade_log_path": None,  # Не пишем логи сделок во время оптимизации
                "strategy_config": strategy_config_override,  # Передаем измененные конфиги
                "risk_config": risk_config_override,
            }

            # --- 4. Запускаем бэктест ---
            backtest_results  = run_backtest_session(backtest_config)

            # --- 5. Анализируем результат и возвращаем метрику ---
            if backtest_results ["status"] != "success" or backtest_results ["trades_df"].empty:
                # Если бэктест упал или не было сделок, "наказываем" эту комбинацию параметров.
                # Pruning - механизм Optuna для досрочного отсечения бесперспективных веток.
                raise optuna.TrialPruned()

            calmar_ratio = _calculate_calmar_ratio(backtest_results ["trades_df"], backtest_results ["initial_capital"])
            return calmar_ratio

        except optuna.TrialPruned:
            raise
        except Exception as e:
            # Если произошла любая другая ошибка, тоже считаем попытку неудачной.
            print(f"Ошибка в trial: {e}")
            return -1.0