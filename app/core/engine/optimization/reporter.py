"""
Модуль генерации отчетов оптимизации (WFO Reporter).

Отвечает за сохранение и визуализацию результатов процесса Walk-Forward Optimization.
Генерирует комплексный пакет документов:
1.  **CSV-сводка**: Таблица с лучшими параметрами и метриками для каждого шага WFO.
2.  **HTML-графики**: Интерактивные визуализации Optuna (история поиска, фронт Парето) для последнего шага.
3.  **Итоговый график**: Кривая капитала на OOS-данных (склеенная из всех шагов),
    показывающая, как стратегия вела бы себя в реальности.
"""

import os
import pandas as pd
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable

import optuna

from app.core.analysis.session import AnalysisSession
from app.core.analysis.constants import METRIC_CONFIG
from app.infrastructure.feeds.backtest.local import BacktestDataLoader
from app.shared.config import config

BACKTEST_CONFIG = config.BACKTEST_CONFIG
PATH_CONFIG = config.PATH_CONFIG

logger = logging.getLogger(__name__)


class OptimizationReporter:
    """
    Генератор отчетов по результатам оптимизации.

    Агрегирует данные из всех шагов WFO и использует различные инструменты
    визуализации (Pandas, Plotly через Optuna, Matplotlib через AnalysisSession)
    для создания финальных артефактов.

    Attributes:
        settings (Dict): Настройки запуска оптимизации.
        all_oos_trades (List[pd.DataFrame]): Список таблиц сделок с каждого OOS-периода.
        step_results (List[Dict]): Метаданные лучших решений на каждом шаге.
        last_study (optuna.Study): Объект исследования Optuna с последнего шага (для детальных графиков).
        base_filepath (str): Базовый путь и имя файла для сохранения отчетов.
    """

    def __init__(self,
                 settings: Dict,
                 all_oos_trades: List[pd.DataFrame],
                 step_results: List[Dict],
                 last_study: Optional[optuna.Study]):
        """
        Инициализирует репортер.

        Args:
            settings (Dict): Конфигурация.
            all_oos_trades (List[pd.DataFrame]): Сделки OOS.
            step_results (List[Dict]): Результаты шагов.
            last_study (Optional[optuna.Study]): Study последнего шага.
        """
        self.settings = settings
        self.all_oos_trades = all_oos_trades
        self.step_results = step_results
        self.last_study = last_study
        self.base_filepath = self._create_base_filepath()

    def _create_base_filepath(self) -> str:
        """
        Формирует уникальное имя файла на основе времени и параметров.

        Returns:
            str: Полный путь без расширения (например, 'reports/opt/20231025_WFO_SMA_BTCUSDT').
        """
        report_dir = PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"]
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Определяем имя цели (тикер или "Portfolio_N")
        instrument_name = (
            f"Portfolio_{len(self.settings['instrument_list'])}"
            if "portfolio_path" in self.settings and self.settings["portfolio_path"]
            else self.settings.get("instrument")
        )

        filename = f"{timestamp}_WFO_{self.settings['strategy']}_{instrument_name}"
        return os.path.join(report_dir, filename)

    def _create_hover_text(self, trials: List[optuna.trial.FrozenTrial]) -> List[str]:
        """
        Генерирует HTML-текст для всплывающих подсказок на графиках Plotly.
        Показывает значения всех параметров при наведении на точку.
        """
        return ["<br>".join([f"&nbsp;&nbsp;<b>{k}</b>: {v}" for k, v in t.params.items()]) for t in trials]

    def _get_plot_targets(self) -> Tuple[Optional[Callable], str]:
        """
        Определяет целевую метрику для оси Y на графиках истории оптимизации.

        Returns:
            Tuple[Callable, str]: Функция извлечения значения и имя метрики.
        """
        # Если оптимизация многокритериальная, берем первую метрику как основную для графика
        is_multi = self.last_study and len(self.last_study.directions) > 1

        if is_multi:
            metric_key = self.settings["metrics"][0]
            metric_name = METRIC_CONFIG[metric_key]['name']
            return lambda t: t.values[0], metric_name

        return None, "Objective Value"

    def _save_optuna_visualizations(self):
        """
        Генерирует и сохраняет интерактивные HTML-отчеты от Optuna.
        Строит графики только для последнего шага WFO (так как хранить все study слишком дорого).
        """
        if not self.last_study:
            return

        logger.info("Сохранение HTML-отчетов Optuna для последнего шага WFO...")

        target_func, target_name = self._get_plot_targets()

        try:
            # 1. График фронта Парето (только для мульти-оптимизации)
            if len(self.last_study.directions) > 1:
                fig = optuna.visualization.plot_pareto_front(
                    self.last_study,
                    target_names=[METRIC_CONFIG[m]['name'] for m in self.settings["metrics"]]
                )
                fig.write_html(f"{self.base_filepath}_last_step_pareto_front.html")

            # 2. График истории оптимизации (сходимость)
            fig_history = optuna.visualization.plot_optimization_history(
                self.last_study, target=target_func, target_name=target_name
            )
            # Добавляем кастомные подсказки с параметрами
            if fig_history.data:
                fig_history.data[0].customdata = self._create_hover_text(self.last_study.trials)
                fig_history.data[0].hovertemplate = (
                    "<b>Trial: %{x}</b><br>Value: %{y:.4f}<br><br>"
                    "<b>Parameters:</b><br>%{customdata}<extra></extra>"
                )
            fig_history.write_html(f"{self.base_filepath}_last_step_history.html")

            # 3. График важности параметров (Feature Importance)
            # Требует минимум 2 завершенных триала и 2 параметра
            completed_trials = self.last_study.get_trials(deepcopy=False, states=[optuna.trial.TrialState.COMPLETE])
            n_params = len(completed_trials[0].params) if completed_trials else 0

            if len(completed_trials) >= 2 and n_params > 1:
                try:
                    fig_importance = optuna.visualization.plot_param_importances(
                        self.last_study, target=target_func, target_name=target_name
                    )
                    fig_importance.write_html(f"{self.base_filepath}_last_step_importance.html")
                except Exception as e:
                    logger.warning(f"Не удалось построить график важности параметров: {e}")

            logger.info("HTML-отчеты Optuna успешно сохранены.")

        except (ValueError, ImportError) as e:
            logger.error(f"Ошибка при генерации отчетов Optuna: {e}")

    def generate_all_reports(self):
        """
        Запускает процесс генерации всех типов отчетов.
        """
        if not self.all_oos_trades:
            logger.error("Нет сделок на OOS-данных. Отчеты не будут сгенерированы.")
            return

        logger.info("\n--- Генерация итоговых отчетов WFO ---")

        # 1. Сохраняем таблицу с параметрами каждого шага
        pd.DataFrame(self.step_results).to_csv(f"{self.base_filepath}_steps_summary.csv", index=False)
        logger.info(f"Сводка по шагам WFO сохранена в: {self.base_filepath}_steps_summary.csv")

        # 2. Сохраняем визуализации Optuna
        self._save_optuna_visualizations()

        # 3. Генерируем стандартный отчет (График эквити OOS)
        # Склеиваем сделки со всех OOS периодов в один DataFrame
        final_trades_df = pd.concat(self.all_oos_trades, ignore_index=True)

        # Для корректного бенчмарка (Buy&Hold) нам нужны котировки за ВЕСЬ период.
        # Берем данные первого инструмента из списка (как прокси для рынка).
        # В идеале для портфеля нужно строить синтетический индекс, но пока берем первый актив.
        instrument_for_bh = self.settings["instrument_list"][0]

        data_handler_bh = BacktestDataLoader(
            exchange=self.settings["exchange"],
            instrument_id=instrument_for_bh,
            interval_str=self.settings["interval"],
            data_path=PATH_CONFIG["DATA_DIR"]
        )
        full_bh_dataset = data_handler_bh.load_raw_data()

        # Запускаем стандартную сессию анализа
        analysis = AnalysisSession(
            trades_df=final_trades_df,
            historical_data=full_bh_dataset,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            exchange=self.settings["exchange"],
            interval=self.settings["interval"],
            risk_manager_type=self.settings["rm"],
            strategy_name=self.settings["strategy"]
        )

        # Сохраняем финальный график и консольный отчет
        analysis.generate_all_reports(
            base_filename=os.path.basename(self.base_filepath),
            report_dir=PATH_CONFIG["REPORTS_OPTIMIZATION_DIR"]
        )