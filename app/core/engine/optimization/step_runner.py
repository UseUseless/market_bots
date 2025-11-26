import os
import pandas as pd
import logging
from tqdm import tqdm
from typing import Dict, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import optuna
from rich.console import Console

from app.core.engine.backtest.runners import _run_and_analyze_single_instrument

from app.core.engine.optimization.objective import Objective
from app.core.analysis.constants import METRIC_CONFIG
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from config import BACKTEST_CONFIG

logger = logging.getLogger(__name__)


class WFOStepRunner:
    """
    Выполняет один полный шаг Walk-Forward Optimization:
    1. In-Sample: Запускает Optuna для поиска лучших параметров.
    2. Out-of-Sample: Запускает бэктест с лучшими параметрами на тестовой выборке.
    """

    def __init__(self, settings: Dict[str, Any], step_num: int, train_slices: Dict, test_slices: Dict):
        self.settings = settings
        self.step_num = step_num
        self.train_slices = train_slices
        self.test_slices = test_slices
        self.console = Console()

    def _run_in_sample_optimization(self) -> optuna.Study:
        # Эта часть остается без изменений
        metrics_to_optimize = self.settings["metrics"]
        directions = [METRIC_CONFIG[m]["direction"] for m in metrics_to_optimize]
        study = optuna.create_study(directions=directions)
        strategy_class = AVAILABLE_STRATEGIES[self.settings["strategy"]]
        objective = Objective(
            strategy_class=strategy_class,
            exchange=self.settings["exchange"],
            interval=self.settings["interval"],
            risk_manager_type=self.settings["rm"],
            train_data_slices=self.train_slices,
            metrics=metrics_to_optimize
        )
        study.optimize(objective, n_trials=self.settings["n_trials"], n_jobs=-1, show_progress_bar=True)
        return study

    def _select_best_trial(self, study: optuna.Study) -> optuna.trial.FrozenTrial:
        # Эта часть остается без изменений
        if not study.best_trials:
            return None
        if len(study.directions) == 1:
            return study.best_trial
        pareto_front = study.best_trials
        tqdm.write(f"Шаг {self.step_num}: Найдено {len(pareto_front)} недоминируемых решений (фронт Парето).")
        tie_breaker_metric = "calmar_ratio"
        direction = METRIC_CONFIG[tie_breaker_metric]['direction']
        default_value = -1e9 if direction == 'maximize' else 1e9
        selector_func = max if direction == 'maximize' else min
        best_trial = selector_func(
            pareto_front,
            key=lambda t: t.user_attrs.get(tie_breaker_metric, default_value)
        )
        tie_breaker_value = best_trial.user_attrs.get(tie_breaker_metric, float('nan'))
        tqdm.write(
            f"Для OOS-теста выбрано решение #{best_trial.number} "
            f"по решающей метрике '{METRIC_CONFIG[tie_breaker_metric]['name']}' (значение: {tie_breaker_value:.4f})."
        )
        return best_trial

    def _run_out_of_sample_test(self, best_trial: optuna.trial.FrozenTrial) -> pd.DataFrame:
        """
        Запускает бэктест с лучшими параметрами на OOS-данных,
        переиспользуя _run_and_analyze_single_instrument.
        """
        strategy_class = AVAILABLE_STRATEGIES[self.settings["strategy"]]
        rm_class = AVAILABLE_RISK_MANAGERS[self.settings["rm"]]

        best_params = best_trial.params
        strategy_params = {k: v for k, v in best_params.items() if not k.startswith("rm_")}
        rm_params = {k[3:]: v for k, v in best_params.items() if k.startswith("rm_")}

        # --- Подготовка задач для пула потоков ---
        tasks = []
        for instrument, oos_slice in self.test_slices.items():
            task_settings = {
                **self.settings,
                "instrument": instrument,
                "data_slice": oos_slice,
                "strategy_class": strategy_class,
                "risk_manager_type": self.settings["rm"],
                "strategy_params": {**strategy_class.get_default_params(), **strategy_params},
                "risk_manager_params": {**rm_class.get_default_params(), **rm_params},
                "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"] / len(self.settings["instrument_list"]),
                "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
                "trade_log_path": None,
            }
            tasks.append(task_settings)

        # --- Запуск OOS-тестов в несколько потоков ---
        all_oos_trades = []
        # Можно запускать и в один поток, если OOS-инструментов мало, но ThreadPool не помешает
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            future_to_settings = {executor.submit(_run_and_analyze_single_instrument, task): task for task in tasks}

            for future in as_completed(future_to_settings):
                analysis_results = future.result()
                if analysis_results and not analysis_results["trades_df"].empty:
                    all_oos_trades.append(analysis_results["trades_df"])

        return pd.concat(all_oos_trades, ignore_index=True) if all_oos_trades else pd.DataFrame()

    def run(self) -> Tuple[pd.DataFrame, Dict, optuna.Study]:
        """Запускает полный шаг WFO и возвращает результаты."""
        # Эта часть остается без изменений
        tqdm.write(f"\n--- Шаг {self.step_num} ---")
        study = self._run_in_sample_optimization()
        best_trial = self._select_best_trial(study)

        if best_trial:
            step_summary = {
                "step": self.step_num, "status": "SUCCESS", "best_trial_number": best_trial.number,
                **best_trial.user_attrs, **best_trial.params
            }
            oos_trades_df = self._run_out_of_sample_test(best_trial)
            tqdm.write(f"Шаг {self.step_num}: OOS-тест дал {len(oos_trades_df)} сделок.")
            return oos_trades_df, step_summary, study
        else:
            tqdm.write(f"Шаг {self.step_num}: Optuna не нашла решений. Пропускаем OOS-тест.")
            empty_summary = {"step": self.step_num, "status": "NO_SOLUTION"}
            return pd.DataFrame(), empty_summary, study