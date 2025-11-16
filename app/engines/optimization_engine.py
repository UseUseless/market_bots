import os
import pandas as pd
import logging
from datetime import datetime
from tqdm import tqdm
from typing import List, Tuple, Dict, Any

import optuna
from rich.console import Console

from app.optimization.objective import Objective
from app.optimization.splitter import split_data_by_periods
from app.core.data.local_handler import HistoricLocalDataHandler
from app.engines.backtest_engine import BacktestEngine
from app.analyzers.metrics.portfolio_metrics import METRIC_CONFIG
from app.analyzers.analysis_session import AnalysisSession
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS
from config import BACKTEST_CONFIG, PATH_CONFIG

logger = logging.getLogger(__name__)


class WFODataPreparer:
    """Отвечает за загрузку и нарезку данных для WFO."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def prepare(self) -> Tuple[Dict[str, List[pd.DataFrame]], int]:
        logger.info("--- Предварительная загрузка и нарезка данных ---")
        all_instrument_periods = {}
        instrument_list = self.settings["instrument_list"]

        for instrument in tqdm(instrument_list, desc="Подготовка данных"):
            data_handler = HistoricLocalDataHandler(
                exchange=self.settings["exchange"],
                instrument_id=instrument,
                interval_str=self.settings["interval"],
                data_path=PATH_CONFIG["DATA_DIR"]
            )
            full_dataset = data_handler.load_raw_data()
            if full_dataset.empty:
                logger.warning(f"Не удалось загрузить данные для {instrument}. Пропускаем.")
                continue
            all_instrument_periods[instrument] = split_data_by_periods(full_dataset, self.settings["total_periods"])

        if not all_instrument_periods:
            raise FileNotFoundError("Не удалось загрузить данные ни для одного инструмента.")

        first_instrument_periods = next(iter(all_instrument_periods.values()))
        num_steps = len(first_instrument_periods) - self.settings["train_periods"] - self.settings["test_periods"] + 1
        if num_steps <= 0:
            raise ValueError("Недостаточно данных для WFO с заданными параметрами.")

        return all_instrument_periods, num_steps


class WFOStepRunner:
    """Выполняет один шаг WFO: In-Sample оптимизацию и Out-of-Sample тест."""

    def __init__(self, settings: Dict[str, Any], step_num: int, train_slices: Dict, test_slices: Dict):
        self.settings = settings
        self.step_num = step_num
        self.train_slices = train_slices
        self.test_slices = test_slices
        self.console = Console()

    def _run_in_sample_optimization(self) -> optuna.Study:
        """Запускает Optuna на обучающей выборке."""
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
        """Выбирает лучший триал из завершенного исследования."""
        if not study.best_trials:
            return None

        if len(study.directions) == 1:
            return study.best_trial

        pareto_front = study.best_trials
        tqdm.write(f"Шаг {self.step_num}: Найдено {len(pareto_front)} недоминируемых решений (фронт Парето).")

        # <<< ИЗМЕНЕНИЕ 1: Логика выбора решающей метрики стала умнее >>>
        # В качестве решающей метрики (tie-breaker) используем Calmar Ratio как надежный баланс риска и доходности.
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
        """Запускает бэктест с лучшими параметрами на OOS-данных."""
        all_oos_instrument_trades = []
        strategy_class = AVAILABLE_STRATEGIES[self.settings["strategy"]]
        rm_class = AVAILABLE_RISK_MANAGERS[self.settings["rm"]]

        best_params = best_trial.params
        strategy_params = {k: v for k, v in best_params.items() if not k.startswith("rm_")}
        rm_params = {k[3:]: v for k, v in best_params.items() if k.startswith("rm_")}

        for instrument, oos_slice in self.test_slices.items():
            backtest_settings = {
                **self.settings,
                "instrument": instrument,
                "data_slice": oos_slice,
                "strategy_params": {**strategy_class.get_default_params(), **strategy_params},
                "risk_manager_params": {**rm_class.get_default_params(), **rm_params},
                "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"] / len(self.settings["instrument_list"]),
                "strategy_class": strategy_class,
                "trade_log_path": None  # Отключаем логирование сделок для OOS-тестов
            }

            backtest_engine = BacktestEngine(backtest_settings)
            oos_results = backtest_engine.run()

            if oos_results["status"] == "success" and not oos_results["trades_df"].empty:
                all_oos_instrument_trades.append(oos_results["trades_df"])

        return pd.concat(all_oos_instrument_trades, ignore_index=True) if all_oos_instrument_trades else pd.DataFrame()

    def run(self) -> Tuple[pd.DataFrame, Dict, optuna.Study]:
        """Запускает полный шаг и возвращает результаты."""
        tqdm.write(f"\n--- Шаг {self.step_num} ---")
        study = self._run_in_sample_optimization()
        best_trial = self._select_best_trial(study)

        # <<< ИЗМЕНЕНИЕ 2: Возвращаем консистентный результат даже при неудаче >>>
        if best_trial:
            step_summary = {"step": self.step_num, "status": "SUCCESS", "best_trial_number": best_trial.number,
                            **best_trial.user_attrs, **best_trial.params}
            oos_trades_df = self._run_out_of_sample_test(best_trial)
            tqdm.write(f"Шаг {self.step_num}: OOS-тест дал {len(oos_trades_df)} сделок.")
            return oos_trades_df, step_summary, study
        else:
            tqdm.write(f"Шаг {self.step_num}: Optuna не нашла решений. Пропускаем OOS-тест.")
            empty_summary = {"step": self.step_num, "status": "NO_SOLUTION"}
            return pd.DataFrame(), empty_summary, study


class OptimizationReporter:
    """Собирает результаты и генерирует все финальные отчеты."""

    def __init__(self, settings: Dict, all_oos_trades: List[pd.DataFrame], step_results: List[Dict],
                 last_study: optuna.Study):
        self.settings = settings
        self.all_oos_trades = all_oos_trades
        self.step_results = step_results
        self.last_study = last_study
        self.base_filename = self._create_base_filename()

    def _create_base_filename(self) -> str:
        report_dir = PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"]
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        instrument_name = self.settings.get("instrument") or f"Portfolio_{len(self.settings['instrument_list'])}"
        return os.path.join(report_dir, f"{timestamp}_WFO_{self.settings['strategy']}_{instrument_name}")

    def _create_hover_text(self, trials: List[optuna.trial.FrozenTrial]) -> List[str]:
        return ["<br>".join([f"&nbsp;&nbsp;<b>{k}</b>: {v}" for k, v in t.params.items()]) for t in trials]

    # <<< ИЗМЕНЕНИЕ 3: Логика вынесена в helper для соблюдения DRY >>>
    def _get_plot_targets(self) -> tuple:
        """Возвращает цель и имя цели для графиков Optuna."""
        is_multi = len(self.last_study.directions) > 1
        if is_multi:
            return lambda t: t.values[0], METRIC_CONFIG[self.settings["metrics"][0]]['name']
        return None, "Objective Value"

    def _save_optuna_visualizations(self):
        if not self.last_study: return
        logger.info("Сохранение HTML-отчетов Optuna для последнего шага WFO...")

        target_func, target_name = self._get_plot_targets()

        try:
            if len(self.last_study.directions) > 1:
                fig = optuna.visualization.plot_pareto_front(self.last_study,
                                                             target_names=[METRIC_CONFIG[m]['name'] for m in
                                                                           self.settings["metrics"]])
                fig.write_html(f"{self.base_filename}_last_step_pareto_front.html")

            fig_history = optuna.visualization.plot_optimization_history(self.last_study, target=target_func,
                                                                         target_name=target_name)
            if fig_history.data:
                fig_history.data[0].customdata = self._create_hover_text(self.last_study.trials)
                fig_history.data[
                    0].hovertemplate = "<b>Trial: %{x}</b><br>Value: %{y:.4f}<br><br><b>Parameters:</b><br>%{customdata}<extra></extra>"
            fig_history.write_html(f"{self.base_filename}_last_step_history.html")

            if len(self.last_study.get_trials(deepcopy=False, states=[optuna.trial.TrialState.COMPLETE])) >= 2:
                fig_importance = optuna.visualization.plot_param_importances(self.last_study, target=target_func,
                                                                             target_name=target_name)
                fig_importance.write_html(f"{self.base_filename}_last_step_importance.html")

            logger.info("HTML-отчеты Optuna успешно сохранены.")
        except (ValueError, ImportError) as e:
            logger.error(f"Не удалось сохранить HTML-отчеты Optuna: {e}")

    def generate(self):
        if not self.all_oos_trades:
            logger.error("Нет сделок на OOS-данных. Отчеты не будут сгенерированы.")
            return

        logger.info("\n--- Генерация итоговых отчетов WFO ---")
        pd.DataFrame(self.step_results).to_csv(f"{self.base_filename}_steps_summary.csv", index=False)
        logger.info("Сводка по шагам WFO сохранена.")
        self._save_optuna_visualizations()

        final_trades_df = pd.concat(self.all_oos_trades, ignore_index=True)
        instrument_for_bh = self.settings["instrument_list"][0]
        data_handler_bh = HistoricLocalDataHandler(
            exchange=self.settings["exchange"], instrument_id=instrument_for_bh,
            interval_str=self.settings["interval"], data_path=PATH_CONFIG["DATA_DIR"]
        )
        full_bh_dataset = data_handler_bh.load_raw_data()

        analysis = AnalysisSession(
            trades_df=final_trades_df, historical_data=full_bh_dataset,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"], exchange=self.settings["exchange"],
            interval=self.settings["interval"], risk_manager_type=self.settings["rm"]
        )
        analysis.generate_all_reports(
            base_filename=os.path.basename(self.base_filename),
            report_dir=PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"]
        )


class OptimizationEngine:
    """Оркестратор для запуска процесса Walk-Forward Optimization."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = self._prepare_settings(settings)

    def _prepare_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Дополняет settings ключами, которые нужны внутренним компонентам."""
        if "portfolio_path" in settings and settings["portfolio_path"]:
            settings["instrument_list"] = sorted(
                [f.replace('.parquet', '') for f in os.listdir(settings["portfolio_path"]) if f.endswith('.parquet')])
        else:
            settings["instrument_list"] = [settings["instrument"]]
        return settings

    def run(self):
        """Запускает полный процесс WFO."""
        try:
            preparer = WFODataPreparer(self.settings)
            all_instrument_periods, num_steps = preparer.prepare()

            all_oos_trades, step_results, last_study = [], [], None

            for step_num in tqdm(range(1, num_steps + 1), desc="Общий прогресс WFO"):
                train_start, train_end = step_num - 1, step_num - 1 + self.settings["train_periods"]
                test_start, test_end = train_end, train_end + self.settings["test_periods"]

                train_slices = {i: pd.concat(p[train_start:train_end]) for i, p in all_instrument_periods.items()}
                test_slices = {i: pd.concat(p[test_start:test_end]) for i, p in all_instrument_periods.items()}

                step_runner = WFOStepRunner(self.settings, step_num, train_slices, test_slices)
                oos_trades_df, step_summary, study = step_runner.run()

                if not oos_trades_df.empty: all_oos_trades.append(oos_trades_df)
                if step_summary: step_results.append(step_summary)
                last_study = study

            reporter = OptimizationReporter(self.settings, all_oos_trades, step_results, last_study)
            reporter.generate()

        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Ошибка подготовки WFO: {e}")
        except Exception:
            logger.critical("Произошла непредвиденная ошибка в процессе WFO!", exc_info=True)