import os
import pandas as pd
import logging
from datetime import datetime
from typing import List, Dict

import optuna

from app.core.analysis.session import AnalysisSession
from app.core.analysis.constants import METRIC_CONFIG
from app.infrastructure.feeds.local import HistoricLocalDataHandler
from app.shared.config import config

BACKTEST_CONFIG = config.BACKTEST_CONFIG
PATH_CONFIG = config.PATH_CONFIG

logger = logging.getLogger(__name__)


class OptimizationReporter:
    """
    Собирает результаты всех шагов WFO и генерирует итоговые отчеты:
    - CSV-файл со сводкой по каждому шагу.
    - HTML-визуализации от Optuna для последнего шага.
    - Стандартный графический и консольный отчет по совокупным OOS-сделкам.
    """

    def __init__(self, settings: Dict, all_oos_trades: List[pd.DataFrame],
                 step_results: List[Dict], last_study: optuna.Study):
        """
        Инициализирует генератор отчетов.

        :param settings: Общие настройки оптимизации.
        :param all_oos_trades: Список DataFrame'ов, где каждый df - сделки одного OOS-шага.
        :param step_results: Список словарей со сводной информацией по каждому шагу.
        :param last_study: Объект Study от Optuna, соответствующий последнему шагу WFO.
        """
        self.settings = settings
        self.all_oos_trades = all_oos_trades
        self.step_results = step_results
        self.last_study = last_study
        self.base_filepath = self._create_base_filepath()

    def _create_base_filepath(self) -> str:
        """Создает базовый путь и имя файла для всех отчетов этого запуска (без расширения)."""
        report_dir = PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"]
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Определяем, это портфель или один инструмент
        instrument_name = (
            f"Portfolio_{len(self.settings['instrument_list'])}"
            if "portfolio_path" in self.settings and self.settings["portfolio_path"]
            else self.settings.get("instrument")
        )

        filename = f"{timestamp}_WFO_{self.settings['strategy']}_{instrument_name}"
        return os.path.join(report_dir, filename)

    def _create_hover_text(self, trials: List[optuna.trial.FrozenTrial]) -> List[str]:
        """Создает текст для всплывающих подсказок в графиках Optuna."""
        return ["<br>".join([f"&nbsp;&nbsp;<b>{k}</b>: {v}" for k, v in t.params.items()]) for t in trials]

    def _get_plot_targets(self) -> tuple:
        """Возвращает цель и имя цели для графиков Optuna."""
        is_multi = len(self.last_study.directions) > 1
        if is_multi:
            # Для мульти-оптимизации берем первую метрику как основную для некоторых графиков
            return lambda t: t.values[0], METRIC_CONFIG[self.settings["metrics"][0]]['name']
        return None, "Objective Value"

    def _save_optuna_visualizations(self):
        """Сохраняет HTML-отчеты от Optuna для последнего шага WFO."""
        if not self.last_study:
            return
        logger.info("Сохранение HTML-отчетов Optuna для последнего шага WFO...")

        target_func, target_name = self._get_plot_targets()

        try:
            # График фронта Парето (только для мульти-оптимизации)
            if len(self.last_study.directions) > 1:
                fig = optuna.visualization.plot_pareto_front(
                    self.last_study,
                    target_names=[METRIC_CONFIG[m]['name'] for m in self.settings["metrics"]]
                )
                fig.write_html(f"{self.base_filepath}_last_step_pareto_front.html")

            # График истории оптимизации
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

            # График важности параметров
            if len(self.last_study.get_trials(deepcopy=False, states=[optuna.trial.TrialState.COMPLETE])) >= 2:
                fig_importance = optuna.visualization.plot_param_importances(
                    self.last_study, target=target_func, target_name=target_name
                )
                fig_importance.write_html(f"{self.base_filepath}_last_step_importance.html")

            logger.info("HTML-отчеты Optuna успешно сохранены.")
        except (ValueError, ImportError) as e:
            logger.error(f"Не удалось сохранить HTML-отчеты Optuna: {e}")

    def generate_all_reports(self):
        """Главный метод, запускающий генерацию всех отчетов."""
        if not self.all_oos_trades:
            logger.error("Нет сделок на OOS-данных. Отчеты не будут сгенерированы.")
            return

        logger.info("\n--- Генерация итоговых отчетов WFO ---")

        # 1. Сохраняем сводку по шагам
        pd.DataFrame(self.step_results).to_csv(f"{self.base_filepath}_steps_summary.csv", index=False)
        logger.info(f"Сводка по шагам WFO сохранена в: {self.base_filepath}_steps_summary.csv")

        # 2. Сохраняем визуализации Optuna
        self._save_optuna_visualizations()

        # 3. Собираем все OOS-сделки и запускаем стандартный анализ
        final_trades_df = pd.concat(self.all_oos_trades, ignore_index=True)

        # Для Buy&Hold бенчмарка нам нужен полный набор данных по одному из инструментов
        instrument_for_bh = self.settings["instrument_list"][0]
        data_handler_bh = HistoricLocalDataHandler(
            exchange=self.settings["exchange"], instrument_id=instrument_for_bh,
            interval_str=self.settings["interval"], data_path=PATH_CONFIG["DATA_DIR"]
        )
        full_bh_dataset = data_handler_bh.load_raw_data()

        analysis = AnalysisSession(
            trades_df=final_trades_df,
            historical_data=full_bh_dataset,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            exchange=self.settings["exchange"],
            interval=self.settings["interval"],
            risk_manager_type=self.settings["rm"],
            strategy_name=self.settings["strategy"]
        )

        analysis.generate_all_reports(
            base_filename=os.path.basename(self.base_filepath),
            report_dir=PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"]
        )