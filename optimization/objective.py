import optuna
import pandas as pd
from typing import Type

from core.backtest_engine import run_backtest_session
from config import BACKTEST_CONFIG, EXCHANGE_SPECIFIC_CONFIG
from strategies.base_strategy import BaseStrategy
from core.risk_manager import AVAILABLE_RISK_MANAGERS
from optimization.metrics import MetricsCalculator

class Objective:
    """Класс-обертка для целевой функции, чтобы передавать статичные параметры."""

    def __init__(self, strategy_class: Type[BaseStrategy], exchange: str, instrument: str,
                 interval: str, risk_manager_type: str, data_slice: pd.DataFrame,
                 metric: str):
        self.strategy_class = strategy_class
        self.exchange = exchange
        self.instrument = instrument
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.data_slice = data_slice
        self.metric = metric
        self.annualization_factor = EXCHANGE_SPECIFIC_CONFIG[exchange]["SHARPE_ANNUALIZATION_FACTOR"]

    def __call__(self, trial: optuna.Trial) -> float:
        """Одна итерация оптимизации."""
        try:
            # 1. Собираем параметры для стратегии
            strategy_params = {}
            strategy_full_config = {}
            for base in reversed(self.strategy_class.__mro__):
                if hasattr(base, 'params_config'):
                    strategy_full_config.update(base.params_config)

            for name, config in strategy_full_config.items():
                if config.get("optimizable", False):
                    if config["type"] == "int":
                        strategy_params[name] = trial.suggest_int(name, config["low"], config["high"],
                                                                  step=config.get("step", 1))
                    elif config["type"] == "float":
                        strategy_params[name] = trial.suggest_float(name, config["low"], config["high"],
                                                                    step=config.get("step"))
                else:
                    strategy_params[name] = config["default"]

            # 2. Собираем параметры для риск-менеджера
            rm_class = AVAILABLE_RISK_MANAGERS[self.risk_manager_type]
            rm_params = {}
            rm_full_config = {}
            for base in reversed(rm_class.__mro__):
                if hasattr(base, 'params_config'):
                    rm_full_config.update(base.params_config)

            for name, config in rm_full_config.items():
                if config.get("optimizable", False):
                    optuna_name = f"rm_{name}"
                    if config["type"] == "int":
                        rm_params[name] = trial.suggest_int(optuna_name, config["low"], config["high"],
                                                            step=config.get("step", 1))
                    elif config["type"] == "float":
                        rm_params[name] = trial.suggest_float(optuna_name, config["low"], config["high"],
                                                              step=config.get("step"))
                else:
                    rm_params[name] = config["default"]

            # 3. Готовим настройки для бэктеста
            backtest_settings = {
                "strategy_class": self.strategy_class,
                "exchange": self.exchange,
                "instrument": self.instrument,
                "interval": self.interval,
                "risk_manager_type": self.risk_manager_type,
                "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
                "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
                "data_dir": "data",
                "trade_log_path": None,
                "strategy_params": strategy_params,
                "risk_manager_params": rm_params,
                "data_slice": self.data_slice
            }

            # 4. Запускаем бэктест
            backtest_results = run_backtest_session(backtest_settings)

            # --- 5. Анализируем результат и возвращаем метрику ---
            if backtest_results ["status"] != "success" or backtest_results ["trades_df"].empty:
                # Если бэктест упал или не было сделок, "наказываем" эту комбинацию параметров.
                # Pruning - механизм Optuna для досрочного отсечения бесперспективных веток.
                raise optuna.TrialPruned()

            trades_df = backtest_results["trades_df"]
            initial_capital = backtest_results["initial_capital"]

            calculator = MetricsCalculator(trades_df, initial_capital, self.annualization_factor)

            value = calculator.calculate(self.metric)

            return value

        except optuna.TrialPruned:
            raise
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Ошибка в trial #{trial.number}: {e}", exc_info=True)
            return -1.0