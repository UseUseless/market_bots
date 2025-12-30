"""
Модуль генерации отчетов оптимизации.

Отвечает за сохранение результатов Walk-Forward Optimization:
1. Таблица параметров по шагам (CSV).
2. Графики поиска Optuna (HTML).
3. Итоговый анализ OOS (Out-of-Sample) эквити.
"""

import os
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable, Any

import pandas as pd
import optuna

from app.core.analysis.session import AnalysisSession
from app.core.analysis.constants import METRIC_CONFIG
from app.infrastructure.feeds.backtest.provider import BacktestDataLoader
from app.shared.config import config

BACKTEST_CONFIG = config.BACKTEST_CONFIG
PATH_CONFIG = config.PATH_CONFIG

logger = logging.getLogger(__name__)


class WFOReportGenerator:
    """
    Генератор отчетов по результатам оптимизации.

    Attributes:
        settings (Dict): Настройки запуска оптимизации.
        all_oos_trades (List[pd.DataFrame]): Список таблиц сделок с каждого OOS-периода.
        step_results (List[Dict]): Метаданные лучших решений на каждом шаге.
        last_study (optuna.Study): Объект исследования Optuna с последнего шага.
    """

    def __init__(self,
                 settings: Dict[str, Any],
                 all_oos_trades: List[pd.DataFrame],
                 step_results: List[Dict],
                 last_study: Optional[optuna.Study]):
        self.settings = settings
        self.all_oos_trades = all_oos_trades
        self.step_results = step_results
        self.last_study = last_study
        self.base_filepath = self._create_base_filepath()

    def _create_base_filepath(self) -> str:
        """Формирует уникальное имя файла для отчетов."""
        report_dir = PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"]
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Определяем имя (тикер или Portfolio_N)
        if self.settings.get("portfolio_path"):
            count = len(self.settings.get("instrument_list", []))
            instrument_name = f"Portfolio_{count}"
        else:
            instrument_name = self.settings.get("instrument", "Unknown")

        filename = f"{timestamp}_WFO_{self.settings['strategy']}_{instrument_name}"
        return os.path.join(report_dir, filename)

    def _create_hover_text(self, trials: List[optuna.trial.FrozenTrial]) -> List[str]:
        """HTML-подсказки для графиков Plotly."""
        return ["<br>".join([f"&nbsp;&nbsp;<b>{k}</b>: {v}" for k, v in t.params.items()]) for t in trials]

    def _get_plot_targets(self) -> Tuple[Optional[Callable], str]:
        """Определяет целевую метрику для оси Y."""
        # Если мульти-критериальная, берем первую метрику
        is_multi = self.last_study and len(self.last_study.directions) > 1

        if is_multi:
            metric_key = self.settings["metrics"][0]
            metric_name = METRIC_CONFIG.get(metric_key, {}).get('name', metric_key)
            return lambda t: t.values[0], metric_name

        return None, "Objective Value"

    def _save_optuna_visualizations(self):
        """Генерирует HTML-отчеты от Optuna."""
        if not self.last_study:
            return

        logger.info("Сохранение HTML-отчетов Optuna...")
        target_func, target_name = self._get_plot_targets()

        try:
            # 1. Pareto Front (если нужно)
            if len(self.last_study.directions) > 1:
                fig = optuna.visualization.plot_pareto_front(
                    self.last_study,
                    target_names=[METRIC_CONFIG.get(m, {}).get('name', m) for m in self.settings["metrics"]]
                )
                fig.write_html(f"{self.base_filepath}_last_step_pareto_front.html")

            # 2. History
            fig_history = optuna.visualization.plot_optimization_history(
                self.last_study, target=target_func, target_name=target_name
            )
            if fig_history.data:
                fig_history.data[0].customdata = self._create_hover_text(self.last_study.trials)
                fig_history.data[0].hovertemplate = (
                    "<b>Trial: %{x}</b><br>Value: %{y:.4f}<br><br>"
                    "<b>Parameters:</b><br>%{customdata}<extra></extra>"
                )
            fig_history.write_html(f"{self.base_filepath}_last_step_history.html")

            # 3. Param Importance
            completed_trials = self.last_study.get_trials(deepcopy=False, states=[optuna.trial.TrialState.COMPLETE])
            if completed_trials and len(completed_trials[0].params) > 1:
                try:
                    fig_imp = optuna.visualization.plot_param_importances(
                        self.last_study, target=target_func, target_name=target_name
                    )
                    fig_imp.write_html(f"{self.base_filepath}_last_step_importance.html")
                except Exception:
                    pass  # Бывает, если параметров слишком мало или они константы

        except Exception as e:
            logger.error(f"Ошибка при генерации отчетов Optuna: {e}")

    def generate(self):
        """Запуск генерации всех отчетов."""
        if not self.all_oos_trades:
            logger.error("Нет OOS сделок. Отчеты не будут сгенерированы.")
            return

        logger.info("--- Генерация отчетов WFO ---")

        # 1. CSV сводка по шагам
        pd.DataFrame(self.step_results).to_csv(f"{self.base_filepath}_steps_summary.csv", index=False)

        # 2. Optuna HTML
        self._save_optuna_visualizations()

        # 3. Финальный график (Склеенная Equity)
        final_trades_df = pd.concat(self.all_oos_trades, ignore_index=True)

        # Для бенчмарка (Buy & Hold) берем полную историю первого инструмента
        # (или единственного, если тест одного актива)
        # Потому что такая архитектура и в AnalysisSession посчитаются коэффициенты
        benchmark_instrument = self.settings["instrument_list"][0]

        loader = BacktestDataLoader(
            exchange=self.settings["exchange"],
            instrument_id=benchmark_instrument,
            interval_str=self.settings["interval"],
            data_path=PATH_CONFIG["DATA_DIR"]
        )
        full_history_df = loader.load_raw_data()

        # Используем AnalysisSession для стандартного отчета
        analysis = AnalysisSession(
            trades_df=final_trades_df,
            historical_data=full_history_df,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            exchange=self.settings["exchange"],
            interval=self.settings["interval"],
            risk_manager_type=self.settings.get("rm", "FIXED"),
            strategy_name=self.settings["strategy"]
        )

        analysis.generate_all_reports(
            base_filename=os.path.basename(self.base_filepath),
            report_dir=PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"],
            # Передаем метрики последнего шага как результат
            wfo_results=None
        )

        logger.info(f"Отчеты сохранены в: {PATH_CONFIG['REPORTS_OPTIMIZATION_DIR']}")