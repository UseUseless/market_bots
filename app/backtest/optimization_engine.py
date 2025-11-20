import os
import pandas as pd
import logging
from typing import Dict, Any

import optuna

from app.backtest.optimization.preparer import WFODataPreparer
from app.backtest.optimization.step_runner import WFOStepRunner
from app.backtest.optimization.reporter import OptimizationReporter

logger = logging.getLogger(__name__)


class OptimizationEngine:
    """
    Высокоуровневый оркестратор для запуска процесса Walk-Forward Optimization.

    Этот класс не содержит сложной логики. Его задачи:
    1. Подготовить настройки.
    2. Вызвать WFODataPreparer для загрузки и нарезки данных.
    3. В цикле вызывать WFOStepRunner для каждого шага WFO.
    4. Вызвать OptimizationReporter для генерации итоговых отчетов.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Инициализирует движок оптимизации.

        :param settings: Словарь с настройками, полученный из UI-слоя.
        """
        self.settings = self._prepare_settings(settings)

    def _prepare_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Дополняет словарь настроек ключами, которые нужны внутренним компонентам.
        В частности, формирует список инструментов для портфеля.
        """
        # Если указан путь к портфелю, сканируем его и формируем список инструментов
        if "portfolio_path" in settings and settings["portfolio_path"]:
            path = settings["portfolio_path"]
            try:
                settings["instrument_list"] = sorted(
                    [f.replace('.parquet', '') for f in os.listdir(path) if f.endswith('.parquet')]
                )
            except FileNotFoundError:
                logger.error(f"Директория портфеля не найдена: {path}")
                settings["instrument_list"] = []
        else:
            # Если указан один инструмент, создаем список из одного элемента
            settings["instrument_list"] = [settings["instrument"]]

        return settings

    def run(self):
        """
        Запускает полный процесс Walk-Forward Optimization от начала до конца.
        """
        try:
            # --- Шаг 1: Подготовка данных ---
            preparer = WFODataPreparer(self.settings)
            all_instrument_periods, num_steps = preparer.prepare()

            # --- Шаг 2: Цикл WFO ---
            all_oos_trades, step_results = [], []
            last_study: optuna.Study | None = None

            for step_num in range(1, num_steps + 1):
                # Определяем срезы данных для текущего шага
                train_start, train_end = step_num - 1, step_num - 1 + self.settings["train_periods"]
                test_start, test_end = train_end, train_end + self.settings["test_periods"]

                train_slices = {i: pd.concat(p[train_start:train_end]) for i, p in all_instrument_periods.items()}
                test_slices = {i: pd.concat(p[test_start:test_end]) for i, p in all_instrument_periods.items()}

                # Запускаем один шаг
                step_runner = WFOStepRunner(self.settings, step_num, train_slices, test_slices)
                oos_trades_df, step_summary, study = step_runner.run()

                # Собираем результаты
                if not oos_trades_df.empty:
                    all_oos_trades.append(oos_trades_df)
                if step_summary:
                    step_results.append(step_summary)
                last_study = study

            # --- Шаг 3: Генерация отчетов ---
            reporter = OptimizationReporter(self.settings, all_oos_trades, step_results, last_study)
            reporter.generate_all_reports()

        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Ошибка подготовки или выполнения WFO: {e}")
        except Exception:
            logger.critical("Произошла непредвиденная ошибка в процессе WFO!", exc_info=True)