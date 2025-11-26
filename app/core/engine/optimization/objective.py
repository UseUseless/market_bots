import optuna
import pandas as pd
from typing import Type, List, Dict
import queue

from app.core.engine.backtest.loop import BacktestEngine
from config import BACKTEST_CONFIG, EXCHANGE_SPECIFIC_CONFIG, PATH_CONFIG
from app.strategies.base_strategy import BaseStrategy
from app.core.risk_engine.risk_manager import AVAILABLE_RISK_MANAGERS
from app.core.analysis.metrics import PortfolioMetricsCalculator
from app.core.analysis.constants import METRIC_CONFIG
import logging

logger = logging.getLogger(__name__)


class Objective:
    """
    Класс-обертка для целевой функции Optuna.
    Принимает уже подготовленные срезы данных для каждого инструмента.
    """

    def __init__(self, strategy_class: Type[BaseStrategy], exchange: str,
                 interval: str, risk_manager_type: str,
                 train_data_slices: Dict[str, pd.DataFrame],
                 metrics: List[str]):
        self.strategy_class = strategy_class
        self.exchange = exchange
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.train_data_slices = train_data_slices
        self.target_metrics = metrics
        self.instrument_list = list(train_data_slices.keys())
        self.annualization_factor = EXCHANGE_SPECIFIC_CONFIG[exchange]["SHARPE_ANNUALIZATION_FACTOR"]
        self.total_initial_capital = BACKTEST_CONFIG["INITIAL_CAPITAL"]

    def _suggest_params(self, trial: optuna.Trial) -> tuple[dict, dict]:
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
        return strategy_params, rm_params

    def __call__(self, trial: optuna.Trial) -> float | tuple[float, ...]:
        try:
            strategy_params, rm_params = self._suggest_params(trial)
            all_instrument_trades = []
            capital_per_instrument = self.total_initial_capital / len(self.instrument_list)

            for instrument, instrument_data_slice in self.train_data_slices.items():
                if instrument_data_slice.empty:
                    continue

                backtest_settings = {
                    "strategy_class": self.strategy_class, "exchange": self.exchange,
                    "instrument": instrument, "interval": self.interval,
                    "risk_manager_type": self.risk_manager_type,
                    "initial_capital": capital_per_instrument,
                    "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
                    "strategy_params": strategy_params, "risk_manager_params": rm_params,
                    "data_slice": instrument_data_slice,
                    "data_dir": PATH_CONFIG["DATA_DIR"]
                }

                events_queue = queue.Queue()
                engine = BacktestEngine(backtest_settings, events_queue)
                backtest_results = engine.run()

                if backtest_results["status"] == "success" and not backtest_results["trades_df"].empty:
                    all_instrument_trades.append(backtest_results["trades_df"])

            if not all_instrument_trades:
                raise optuna.TrialPruned("Ни на одном инструменте не было совершено сделок.")

            portfolio_trades_df = pd.concat(all_instrument_trades, ignore_index=True)
            portfolio_trades_df.sort_values(by='exit_timestamp_utc', inplace=True)

            calculator = PortfolioMetricsCalculator(portfolio_trades_df, self.total_initial_capital, self.annualization_factor)

            if not calculator.is_valid:
                raise optuna.TrialPruned("Недостаточно сделок для расчета метрик.")

            all_calculated_metrics = calculator.calculate_all()
            for metric_key, value in all_calculated_metrics.items():
                trial.set_user_attr(metric_key, value)

            if len(self.target_metrics) == 1:
                return trial.user_attrs[self.target_metrics[0]]
            else:
                return tuple(trial.user_attrs[m] for m in self.target_metrics)

        except optuna.TrialPruned as e:
            raise e
        except Exception:
            logger.error(f"Критическая ошибка в trial #{trial.number}", exc_info=True)
            if len(self.target_metrics) == 1:
                direction = METRIC_CONFIG[self.target_metrics[0]]['direction']
                return -1e9 if direction == 'maximize' else 1e9
            else:
                return tuple(
                    -1e9 if METRIC_CONFIG[m]['direction'] == 'maximize' else 1e9
                    for m in self.target_metrics
                )