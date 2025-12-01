"""
Модуль выполнения шага оптимизации (WFO Step Runner).

Отвечает за выполнение одной итерации Walk-Forward Optimization.
Каждый шаг состоит из двух фаз:
1.  **In-Sample (IS)**: Оптимизация параметров с помощью Optuna на обучающей выборке.
2.  **Out-of-Sample (OOS)**: Тестирование найденных "лучших" параметров на тестовой выборке
    (данные, которые алгоритм не видел при обучении).
"""

import os
import logging
from typing import Dict, Tuple, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm
import optuna
from rich.console import Console

from app.core.engine.backtest.runners import _run_and_analyze_single_instrument
from app.core.calculations.indicators import FeatureEngine
from app.core.engine.optimization.objective import Objective
from app.core.analysis.constants import METRIC_CONFIG
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.shared.config import config

logger = logging.getLogger(__name__)


class WFOStepRunner:
    """
    Исполнитель одного шага WFO.

    Attributes:
        settings (Dict): Настройки оптимизации.
        step_num (int): Номер текущего шага (для логов).
        train_slices (Dict): Данные для обучения {Тикер: DataFrame}.
        test_slices (Dict): Данные для теста {Тикер: DataFrame}.
        feature_engine (FeatureEngine): Сервис индикаторов.
    """

    def __init__(self,
                 settings: Dict[str, Any],
                 step_num: int,
                 train_slices: Dict[str, pd.DataFrame],
                 test_slices: Dict[str, pd.DataFrame],
                 feature_engine: FeatureEngine):
        """
        Инициализирует раннер.

        Args:
            settings (Dict): Конфигурация.
            step_num (int): Номер шага.
            train_slices (Dict): In-Sample данные.
            test_slices (Dict): Out-of-Sample данные.
            feature_engine (FeatureEngine): Инстанс движка фич.
        """
        self.settings = settings
        self.step_num = step_num
        self.train_slices = train_slices
        self.test_slices = test_slices
        self.feature_engine = feature_engine
        self.console = Console()

    def _run_in_sample_optimization(self) -> optuna.Study:
        """
        Запускает процесс поиска гиперпараметров (Optuna) на In-Sample данных.

        Returns:
            optuna.Study: Завершенное исследование Optuna с результатами всех trial'ов.
        """
        metrics_to_optimize = self.settings["metrics"]

        # Определяем направления оптимизации (max/min) для каждой метрики
        directions = [METRIC_CONFIG[m]["direction"] for m in metrics_to_optimize]

        # Создаем in-memory study (не сохраняем в БД для скорости WFO)
        study = optuna.create_study(directions=directions)

        strategy_class = AVAILABLE_STRATEGIES[self.settings["strategy"]]

        # Инициализируем целевую функцию
        objective = Objective(
            strategy_class=strategy_class,
            exchange=self.settings["exchange"],
            interval=self.settings["interval"],
            risk_manager_type=self.settings["rm"],
            train_data_slices=self.train_slices,
            metrics=metrics_to_optimize,
            feature_engine=self.feature_engine
        )

        # Запускаем оптимизацию. n_jobs=-1 использует все ядра.
        study.optimize(
            objective,
            n_trials=self.settings["n_trials"],
            n_jobs=-1,
            show_progress_bar=True
        )
        return study

    def _select_best_trial(self, study: optuna.Study) -> Optional[optuna.trial.FrozenTrial]:
        """
        Выбирает лучший набор параметров из результатов обучения.

        Логика выбора:
        1. Если метрика одна: берется `study.best_trial`.
        2. Если метрик несколько (Pareto Front):
           - Получаем список недоминируемых решений.
           - Выбираем одно решение на основе "Tie-Breaker Metric" (по умолчанию Calmar Ratio).
           - Это позволяет автоматизировать выбор компромиссного решения.

        Args:
            study (optuna.Study): Завершенное исследование.

        Returns:
            Optional[FrozenTrial]: Лучший Trial или None, если решений нет.
        """
        if not study.best_trials:
            return None

        # Однокритериальная оптимизация
        if len(study.directions) == 1:
            return study.best_trial

        # Многокритериальная оптимизация
        pareto_front = study.best_trials
        tqdm.write(f"Шаг {self.step_num}: Найдено {len(pareto_front)} решений на фронте Парето.")

        # Эвристика: выбираем решение с лучшим Calmar Ratio
        tie_breaker_metric = "calmar_ratio"

        # Если Calmar Ratio нет в конфиге (маловероятно, но для надежности), берем первую метрику
        if tie_breaker_metric not in METRIC_CONFIG:
            tie_breaker_metric = self.settings["metrics"][0]

        direction = METRIC_CONFIG[tie_breaker_metric]['direction']

        # Значение по умолчанию для сортировки (очень плохое число)
        default_value = -1e9 if direction == 'maximize' else 1e9
        selector_func = max if direction == 'maximize' else min

        # Выбираем лучший триал из фронта Парето по дополнительному критерию
        best_trial = selector_func(
            pareto_front,
            key=lambda t: t.user_attrs.get(tie_breaker_metric, default_value)
        )

        tie_value = best_trial.user_attrs.get(tie_breaker_metric, float('nan'))
        tqdm.write(
            f"Выбрано решение #{best_trial.number} по метрике '{METRIC_CONFIG[tie_breaker_metric]['name']}' "
            f"(значение: {tie_value:.2f})."
        )
        return best_trial

    def _run_out_of_sample_test(self, best_trial: optuna.trial.FrozenTrial) -> pd.DataFrame:
        """
        Прогоняет лучший набор параметров на Out-of-Sample данных.

        Это симуляция "реальной торговли" с параметрами, подобранными в прошлом.

        Args:
            best_trial (FrozenTrial): Лучшая попытка из этапа обучения.

        Returns:
            pd.DataFrame: Объединенный DataFrame сделок на OOS периоде по всем инструментам.
        """
        strategy_class = AVAILABLE_STRATEGIES[self.settings["strategy"]]
        rm_class = AVAILABLE_RISK_MANAGERS[self.settings["rm"]]

        # Разделяем параметры обратно на стратегию и РМ
        best_params = best_trial.params
        strategy_params = {k: v for k, v in best_params.items() if not k.startswith("rm_")}
        # Убираем префикс 'rm_'
        rm_params = {k[3:]: v for k, v in best_params.items() if k.startswith("rm_")}

        # Подготовка задач
        tasks = []
        initial_capital = config.BACKTEST_CONFIG["INITIAL_CAPITAL"]
        commission_rate = config.BACKTEST_CONFIG["COMMISSION_RATE"]

        for instrument, oos_slice in self.test_slices.items():
            # Пропускаем, если данных в тесте нет
            if oos_slice.empty:
                continue

            task_settings = {
                **self.settings,
                "instrument": instrument,
                "data_slice": oos_slice,
                "strategy_class": strategy_class,
                "risk_manager_type": self.settings["rm"],
                # Объединяем дефолтные параметры с оптимизированными
                "strategy_params": {**strategy_class.get_default_params(), **strategy_params},
                "risk_manager_params": {**rm_class.get_default_params(), **rm_params},
                "initial_capital": initial_capital,
                "commission_rate": commission_rate,
                "trade_log_path": None,  # Не пишем логи в файл для скорости
            }
            tasks.append(task_settings)

        # Запуск параллельного тестирования
        all_oos_trades = []
        max_workers = os.cpu_count() or 4

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_settings = {
                executor.submit(_run_and_analyze_single_instrument, t): t for t in tasks
            }

            for future in as_completed(future_to_settings):
                try:
                    res = future.result()
                    if res and not res["trades_df"].empty:
                        all_oos_trades.append(res["trades_df"])
                except Exception as e:
                    logger.error(f"Ошибка в OOS тесте: {e}")

        if not all_oos_trades:
            return pd.DataFrame()

        return pd.concat(all_oos_trades, ignore_index=True)

    def run(self) -> Tuple[pd.DataFrame, Dict, optuna.Study]:
        """
        Запускает полный цикл шага WFO.

        1.  In-Sample Optimization.
        2.  Selection of Best Parameters.
        3.  Out-of-Sample Validation.

        Returns:
            Tuple:
                - pd.DataFrame: Сделки на OOS периоде.
                - Dict: Метаданные шага (параметры, статус).
                - optuna.Study: Объект исследования (для репортера).
        """
        tqdm.write(f"\n--- Шаг {self.step_num} ---")

        study = self._run_in_sample_optimization()
        best_trial = self._select_best_trial(study)

        if best_trial:
            step_summary = {
                "step": self.step_num,
                "status": "SUCCESS",
                "best_trial_number": best_trial.number,
                **best_trial.user_attrs,  # Метрики In-Sample
                **best_trial.params  # Лучшие параметры
            }

            # Валидация на будущем
            oos_trades_df = self._run_out_of_sample_test(best_trial)

            tqdm.write(f"Шаг {self.step_num}: OOS-тест дал {len(oos_trades_df)} сделок.")
            return oos_trades_df, step_summary, study
        else:
            tqdm.write(f"Шаг {self.step_num}: Optuna не нашла решений (все trials pruned).")
            empty_summary = {"step": self.step_num, "status": "NO_SOLUTION"}
            return pd.DataFrame(), empty_summary, study