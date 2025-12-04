"""
Модуль движка оптимизации (Optimization Engine).

Этот класс является "сердцем" процесса Walk-Forward Optimization (WFO).
Он координирует работу специализированных компонентов, выполняя процесс шаг за шагом.

Алгоритм работы:
1.  **Подготовка**: `WFODataPreparer` загружает данные и разбивает их на N периодов.
2.  **Цикл WFO**: Движок проходит по периодам с помощью скользящего окна.
    -   Формирует выборку In-Sample (Train) для обучения.
    -   Формирует выборку Out-of-Sample (Test) для проверки.
    -   Запускает `WFOStepRunner` для выполнения оптимизации Optuna на этом шаге.
3.  **Отчетность**: `OptimizationReporter` собирает результаты всех шагов и строит графики.
"""

import os
import logging
from typing import Dict, Any, List

import pandas as pd
import optuna

from app.core.engine.optimization.preparer import WFODataPreparer
from app.core.engine.optimization.step_runner import WFOStepRunner
from app.core.engine.optimization.reporter import OptimizationReporter
from app.core.calculations.indicators import FeatureEngine

logger = logging.getLogger(__name__)


class OptimizationEngine:
    """
    Оркестратор процесса Walk-Forward Optimization.

    Управляет жизненным циклом оптимизации, но не содержит низкоуровневой
    логики работы с Optuna или данными.

    Attributes:
        settings (Dict): Полная конфигурация запуска.
        feature_engine (FeatureEngine): Сервис для расчета индикаторов (DI).
    """

    def __init__(self, settings: Dict[str, Any], feature_engine: FeatureEngine):
        """
        Инициализирует движок.

        Args:
            settings (Dict[str, Any]): Настройки из Runner'а.
            feature_engine (FeatureEngine): Инстанс калькулятора индикаторов.
        """
        self.settings = self._prepare_settings(settings)
        self.feature_engine = feature_engine

    def _prepare_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Валидирует и дополняет настройки перед запуском.

        Определяет список инструментов:
        - Если передан `portfolio_path`, сканирует папку на наличие .parquet файлов.
        - Если передан `instrument`, создает список из одного элемента.

        Args:
            settings (Dict[str, Any]): Исходные настройки.

        Returns:
            Dict[str, Any]: Обновленные настройки с ключом `instrument_list`.
        """
        # Если указан путь к портфелю, сканируем его и формируем список инструментов
        if settings.get("portfolio_path"):
            path = settings["portfolio_path"]
            if not os.path.exists(path):
                # Логируем здесь, но ошибка всплывет в Preparer'е
                logger.error(f"Директория портфеля не найдена: {path}")
                settings["instrument_list"] = []
            else:
                try:
                    # Ищем все .parquet файлы и убираем расширение, чтобы получить тикеры
                    settings["instrument_list"] = sorted(
                        [f.replace('.parquet', '') for f in os.listdir(path) if f.endswith('.parquet')]
                    )
                except Exception as e:
                    logger.error(f"Ошибка при сканировании портфеля: {e}")
                    settings["instrument_list"] = []
        else:
            # Режим одного инструмента
            settings["instrument_list"] = [settings["instrument"]]

        return settings

    def run(self):
        """
        Запускает основной цикл Walk-Forward Optimization.

        Метод не перехватывает исключения. Ошибки должны всплывать в `decorators.py`,
        чтобы обеспечить корректное завершение программы и сброс логов.

        Raises:
            FileNotFoundError: Если нет данных для инструментов.
            ValueError: Если параметры WFO (периоды) некорректны.
        """
        # --- Шаг 1: Подготовка данных ---
        # Загружаем данные и режем их на равные куски (Periods)
        preparer = WFODataPreparer(self.settings)
        all_instrument_periods, num_steps = preparer.prepare()

        logger.info(f"Данные подготовлены. Всего шагов WFO: {num_steps}")

        # --- Шаг 2: Цикл WFO (Rolling Window) ---
        all_oos_trades: List[pd.DataFrame] = []
        step_results: List[Dict] = []
        last_study: optuna.Study | None = None

        for step_num in range(1, num_steps + 1):
            # Рассчитываем индексы скользящего окна
            # Train: [start ... end]
            train_start = step_num - 1
            train_end = train_start + self.settings["train_periods"]

            # Test: [train_end ... end] (идет сразу за обучением)
            test_start = train_end
            test_end = test_start + self.settings["test_periods"]

            # Собираем данные для всех инструментов на этом шаге
            # pd.concat склеивает список периодов (DataFrame-ов) в один большой DF
            train_slices = {
                instr: pd.concat(periods[train_start:train_end])
                for instr, periods in all_instrument_periods.items()
            }
            test_slices = {
                instr: pd.concat(periods[test_start:test_end])
                for instr, periods in all_instrument_periods.items()
            }

            # Инициализируем и запускаем раннер для конкретного шага
            step_runner = WFOStepRunner(
                self.settings,
                step_num,
                train_slices,
                test_slices,
                feature_engine=self.feature_engine
            )

            # Запуск оптимизации (Optuna) и теста на OOS
            oos_trades_df, step_summary, study = step_runner.run()

            # Сохраняем результаты
            if not oos_trades_df.empty:
                all_oos_trades.append(oos_trades_df)

            if step_summary:
                step_results.append(step_summary)

            last_study = study

        # --- Шаг 3: Генерация итоговых отчетов ---
        # Передаем накопленные сделки со всех OOS-периодов
        reporter = OptimizationReporter(self.settings, all_oos_trades, step_results, last_study)
        reporter.generate_all_reports()