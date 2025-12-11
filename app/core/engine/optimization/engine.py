"""
Единый движок Walk-Forward Optimization (WFO).

Этот модуль объединяет в себе логику оптимизации стратегий методом скользящего окна.
Он управляет полным циклом: от загрузки и нарезки данных до запуска Optuna
и агрегации Out-of-Sample результатов.

Архитектура процесса:
1. Подготовка данных: Загрузка истории и разбиение на N частей.
2. Цикл WFO: Сдвиг окна [Train | Test] по истории.
3. In-Sample (Train): Поиск лучших параметров с помощью Optuna.
4. Out-of-Sample (Test): Проверка лучших параметров на "невидимых" данных.
"""

import os
import logging
import concurrent.futures
from typing import Dict, Any, List, Tuple, Optional, Union

import pandas as pd
import numpy as np
import optuna
from tqdm import tqdm

# Импорты ядра
from app.core.engine.backtest.engine import BacktestEngine
from app.core.analysis.metrics import PortfolioMetricsCalculator
from app.core.analysis.constants import METRIC_CONFIG
from app.core.risk import RiskManager
from app.strategies import AVAILABLE_STRATEGIES
from app.shared.schemas import TradingConfig
from app.shared.config import config as app_config

# Импорты инфраструктуры
from app.infrastructure.feeds.backtest.provider import BacktestDataLoader
from app.infrastructure.feeds.backtest.provider import EXCHANGE_SPECIFIC_CONFIG
from app.core.analysis.reports.wfo import WFOReportGenerator

logger = logging.getLogger(__name__)


class WFOEngine:
    """
    Оркестратор процесса Walk-Forward Optimization.

    Управляет жизненным циклом исследования, взаимодействием с Optuna
    и генерацией итоговых отчетов.

    Attributes:
        settings (Dict): Конфигурация запуска (биржа, стратегия, периоды и т.д.).
        all_instrument_periods (Dict): Кэш данных, нарезанных на куски.
        num_steps (int): Вычисленное количество шагов сдвига окна.
        strategy_cls (Type[BaseStrategy]): Класс стратегии для интроспекции параметров.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Инициализирует оптимизатор.

        Args:
            settings: Словарь настроек, полученный из CLI аргументов.
        """
        self.settings = self._prepare_settings(settings)

        # Кэш данных: {instrument_ticker: [df_period_1, df_period_2, ...]}
        self.all_instrument_periods: Dict[str, List[pd.DataFrame]] = {}
        self.num_steps = 0

        # Ссылки на классы стратегии и риска для чтения их конфигурации (params_config)
        self.strategy_cls = AVAILABLE_STRATEGIES[self.settings["strategy"]]
        self.risk_cls = RiskManager

        # Коэффициент аннуализации для корректного расчета Шарпа (Крипта=365, Акции=252)
        exchange_conf = EXCHANGE_SPECIFIC_CONFIG.get(self.settings["exchange"], {})
        self.annual_factor = exchange_conf.get("SHARPE_ANNUALIZATION_FACTOR", 252)

    def _prepare_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Валидация и донастройка параметров запуска.

        Определяет список инструментов: либо один (флаг --instrument),
        либо список из папки (флаг --portfolio-path).
        """
        if settings.get("portfolio_path"):
            path = settings["portfolio_path"]
            if os.path.exists(path):
                # Сканируем папку на наличие .parquet файлов
                settings["instrument_list"] = sorted(
                    [f.replace('.parquet', '') for f in os.listdir(path) if f.endswith('.parquet')]
                )
            else:
                logger.error(f"Portfolio path not found: {path}")
                settings["instrument_list"] = []
        else:
            # Режим одного инструмента
            settings["instrument_list"] = [settings["instrument"]]

        return settings

    def _load_data(self):
        """
        Загружает данные с диска и нарезает их на периоды для WFO.

        Использует BacktestDataLoader для стандартизации процесса загрузки.
        Сохраняет нарезанные данные в self.all_instrument_periods.
        """
        logger.info("Загрузка и нарезка данных для оптимизации...")

        valid_periods_count = None

        for instr in tqdm(self.settings["instrument_list"], desc="Loading Data"):
            loader = BacktestDataLoader(
                exchange=self.settings["exchange"],
                instrument_id=instr,
                interval_str=self.settings["interval"],
                data_path=app_config.PATH_CONFIG["DATA_DIR"]
            )

            # Загрузка и разбиение на N частей
            periods = loader.load_and_split(self.settings["total_periods"])

            if periods:
                self.all_instrument_periods[instr] = periods

                # Валидация целостности: у всех инструментов должно быть одинаковое кол-во кусков
                if valid_periods_count is None:
                    valid_periods_count = len(periods)
                elif len(periods) != valid_periods_count:
                    logger.warning(
                        f"Инструмент {instr} имеет {len(periods)} частей, ожидалось {valid_periods_count}. Исключен."
                    )
                    del self.all_instrument_periods[instr]

        if not self.all_instrument_periods:
            raise FileNotFoundError("Не удалось загрузить валидные данные ни для одного инструмента.")

        # Расчет количества шагов WFO (Rolling Window steps)
        # Формула: Total_Parts - Train_Len - Test_Len + 1
        self.num_steps = (
                valid_periods_count
                - self.settings["train_periods"]
                - self.settings["test_periods"] + 1
        )

        if self.num_steps <= 0:
            raise ValueError(f"Недостаточно периодов ({valid_periods_count}) для заданных окон Train/Test.")

        logger.info(f"Данные готовы. Инструментов: {len(self.all_instrument_periods)}. Шагов WFO: {self.num_steps}.")

    # --- LOGIC: OBJECTIVE FUNCTION (OPTIMIZATION CORE) ---

    def _suggest_params(self, trial: optuna.Trial) -> Tuple[Dict, Dict]:
        """
        Генерирует значения параметров для одной итерации Optuna.

        Читает `params_config` из класса стратегии и риск-менеджера,
        создавая соответствующие `trial.suggest_int` или `trial.suggest_float`.

        Returns:
            Tuple[Dict, Dict]: (strategy_params, risk_config)
        """
        # 1. Параметры Стратегии
        strat_params = {}
        strat_conf_map = {}

        # Собираем конфиги со всех родительских классов (наследование параметров)
        for base in reversed(self.strategy_cls.__mro__):
            if hasattr(base, 'params_config'):
                strat_conf_map.update(base.params_config)

        for name, conf in strat_conf_map.items():
            if conf.get("optimizable", False):
                if conf["type"] == "int":
                    strat_params[name] = trial.suggest_int(name, conf["low"], conf["high"], step=conf.get("step", 1))
                elif conf["type"] == "float":
                    strat_params[name] = trial.suggest_float(name, conf["low"], conf["high"], step=conf.get("step"))
            else:
                # Если параметр не оптимизируемый, берем дефолт
                strat_params[name] = conf.get("default")

        # 2. Параметры Риск-менеджера
        risk_params = {"type": self.settings["rm"]}

        if hasattr(self.risk_cls, 'params_config'):
            risk_conf_map = {}
            for base in reversed(self.risk_cls.__mro__):
                if hasattr(base, 'params_config'):
                    risk_conf_map.update(base.params_config)

            for name, conf in risk_conf_map.items():
                if conf.get("optimizable", False):
                    # Добавляем префикс rm_, чтобы избежать коллизий имен с параметрами стратегии
                    optuna_name = f"rm_{name}"
                    if conf["type"] == "int":
                        risk_params[name] = trial.suggest_int(optuna_name, conf["low"], conf["high"],
                                                              step=conf.get("step", 1))
                    elif conf["type"] == "float":
                        risk_params[name] = trial.suggest_float(optuna_name, conf["low"], conf["high"],
                                                                step=conf.get("step"))
                else:
                    risk_params[name] = conf.get("default")

        return strat_params, risk_params

    @staticmethod
    def _run_single_backtest_memory(config: TradingConfig, data: pd.DataFrame) -> pd.DataFrame:
        """
        Статический метод-воркер для запуска бэктеста в отдельном потоке.
        Принимает DataFrame напрямую из памяти (без чтения с диска).
        """
        engine = BacktestEngine(config=config, data_slice=data)
        res = engine.run()
        if res["status"] == "success":
            return res["trades_df"]
        return pd.DataFrame()

    def _objective(self, trial: optuna.Trial, train_slices: Dict[str, pd.DataFrame]) -> Union[float, Tuple[float, ...]]:
        """
        Целевая функция (Fitness Function) для Optuna.

        Запускает симуляцию с выбранными параметрами на Train-выборке.
        Если инструментов много (портфель), запускает тесты параллельно и агрегирует результат.
        """
        try:
            # 1. Генерация параметров для текущей итерации
            strat_params, risk_config = self._suggest_params(trial)

            # 2. Формирование базового конфига
            base_config = TradingConfig(
                mode="OPTIMIZATION",
                exchange=self.settings["exchange"],
                instrument="TEMP",  # Подменяется в цикле
                interval=self.settings["interval"],
                strategy_name=self.settings["strategy"],
                strategy_params=strat_params,
                risk_config=risk_config,
                initial_capital=app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
                commission_rate=app_config.BACKTEST_CONFIG["COMMISSION_RATE"]
            )

            # 3. Параллельный запуск бэктестов на всех инструментах выборки
            tasks = []
            for instr, data in train_slices.items():
                if not data.empty:
                    # Копия конфига с правильным тикером
                    cfg = base_config.model_copy(update={"instrument": instr})
                    tasks.append((cfg, data))

            all_trades = []
            # ThreadPoolExecutor используется, так как BacktestEngine освобождает GIL при вызовах pandas/numpy
            with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
                futures = {executor.submit(self._run_single_backtest_memory, c, d): c for c, d in tasks}
                for f in concurrent.futures.as_completed(futures):
                    df = f.result()
                    if not df.empty:
                        all_trades.append(df)

            if not all_trades:
                # Если сделок нет вообще, это плохой результат, пруним триал
                raise optuna.TrialPruned("No trades generated.")

            # 4. Расчет метрик портфеля
            # Объединяем сделки со всех инструментов в один поток
            portfolio_trades = pd.concat(all_trades, ignore_index=True)
            if 'exit_timestamp_utc' in portfolio_trades.columns:
                portfolio_trades['exit_timestamp_utc'] = pd.to_datetime(portfolio_trades['exit_timestamp_utc'])
                portfolio_trades.sort_values('exit_timestamp_utc', inplace=True)

            # Общий капитал = Начальный * Кол-во инструментов
            total_capital = base_config.initial_capital * len(train_slices)

            calc = PortfolioMetricsCalculator(portfolio_trades, total_capital, self.annual_factor)

            if not calc.is_valid:
                raise optuna.TrialPruned("Metrics calculation failed (not enough data).")

            metrics = calc.calculate_all()

            # Сохраняем все рассчитанные метрики в атрибуты Trial (для аналитики потом)
            for k, v in metrics.items():
                trial.set_user_attr(k, v)

            # 5. Возврат целевых значений
            # Optuna минимизирует или максимизирует значения в зависимости от настроек study
            targets = self.settings["metrics"]
            results = []
            for m in targets:
                # Если метрики нет (например, Sharpe при 0 сделок), возвращаем "плохое" число
                val = metrics.get(m, -1e9)
                results.append(val)

            return results[0] if len(results) == 1 else tuple(results)

        except optuna.TrialPruned:
            raise
        except Exception:
            # При любой ошибке возвращаем худшее значение, чтобы алгоритм ушел из этой области
            return tuple([-1e9] * len(self.settings["metrics"])) if len(self.settings["metrics"]) > 1 else -1e9

    # --- LOGIC: STEP RUNNER ---

    def _optimize_step(self, step_num: int,
                       train_slices: Dict[str, pd.DataFrame],
                       test_slices: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, Dict, optuna.Study]:
        """
        Выполняет один полный шаг Walk-Forward Optimization.

        1. In-Sample Optimization (Train): Запускает Optuna на train_slices.
        2. Selection: Выбирает лучшие параметры.
        3. Out-of-Sample Test (Test): Прогоняет лучшие параметры на test_slices.

        Returns:
            Tuple: (DataFrame сделок OOS, Словарь лучших параметров, Объект исследования Optuna)
        """
        tqdm.write(f"\n>>> WFO Шаг {step_num}/{self.num_steps}")

        # 1. Создание Study и запуск оптимизации
        directions = [METRIC_CONFIG[m]["direction"] for m in self.settings["metrics"]]
        study = optuna.create_study(directions=directions)

        # lambda нужна для проброса train_slices в objective
        study.optimize(
            lambda t: self._objective(t, train_slices),
            n_trials=self.settings["n_trials"],
            n_jobs=1,  # Параллелизм уже реализован внутри _objective
            show_progress_bar=True
        )

        # 2. Выбор лучшего Trial
        if not study.best_trials:
            logger.warning(f"Шаг {step_num}: Не найдено успешных триалов.")
            return pd.DataFrame(), {"status": "FAILED"}, study

        # Если метрик несколько (Pareto Front), выбираем лучший по первой метрике (Main Objective)
        best_trial = study.best_trials[0]
        if len(study.directions) > 1:
            best_trial = max(study.best_trials, key=lambda t: t.values[0])

        # 3. Подготовка параметров для OOS теста
        best_params = best_trial.params
        # Разделяем параметры обратно на Стратегию и Риск (по префиксу rm_)
        strat_params = {k: v for k, v in best_params.items() if not k.startswith("rm_")}
        risk_params = {k[3:]: v for k, v in best_params.items() if k.startswith("rm_")}

        final_strat = {**self.strategy_cls.get_default_params(), **strat_params}
        final_risk = {**{"type": self.settings["rm"]}, **risk_params}

        # 4. Запуск Out-of-Sample (Test)
        # Проверяем, как найденные параметры работают на "будущем"
        base_config = TradingConfig(
            mode="OPTIMIZATION",
            exchange=self.settings["exchange"],
            instrument="TEMP",
            interval=self.settings["interval"],
            strategy_name=self.settings["strategy"],
            strategy_params=final_strat,
            risk_config=final_risk,
            initial_capital=app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
            commission_rate=app_config.BACKTEST_CONFIG["COMMISSION_RATE"]
        )

        all_oos_trades = []
        # Запускаем последовательно (OOS обычно короткий, накладные расходы на потоки выше)
        for instr, data in test_slices.items():
            if not data.empty:
                cfg = base_config.model_copy(update={"instrument": instr})
                engine = BacktestEngine(config=cfg, data_slice=data)
                res = engine.run()
                if res["status"] == "success" and not res["trades_df"].empty:
                    all_oos_trades.append(res["trades_df"])

        oos_df = pd.concat(all_oos_trades, ignore_index=True) if all_oos_trades else pd.DataFrame()

        # Метаданные шага для отчета
        step_summary = {
            "step": step_num,
            "train_start_idx": list(train_slices.values())[0]['time'].iloc[0],
            "test_start_idx": list(test_slices.values())[0]['time'].iloc[0],
            **best_trial.params,
            **best_trial.user_attrs
        }

        tqdm.write(f"Шаг {step_num} завершен. Сделок на OOS: {len(oos_df)}")
        return oos_df, step_summary, study

    # --- MAIN RUN ---

    def run(self):
        """
        Запускает полный процесс оптимизации.

        1. Загружает данные.
        2. Запускает цикл по шагам (Rolling Window).
        3. Собирает результаты OOS в единый трек-рекорд.
        4. Генерирует итоговые отчеты.
        """
        self._load_data()

        all_oos_trades = []
        step_results = []
        last_study = None

        # Цикл по шагам WFO
        for step_num in range(1, self.num_steps + 1):
            # Расчет индексов периодов (частей)
            # Пример: Total=10, Train=5, Test=1.
            # Step 1: Train=[0:5], Test=[5:6]
            # Step 2: Train=[1:6], Test=[6:7]
            train_start = step_num - 1
            train_end = train_start + self.settings["train_periods"]
            test_start = train_end
            test_end = test_start + self.settings["test_periods"]

            # Формирование DataFrame'ов для текущего шага
            train_slices = {}
            test_slices = {}

            for instr, periods in self.all_instrument_periods.items():
                train_slices[instr] = pd.concat(periods[train_start:train_end], ignore_index=True)
                test_slices[instr] = pd.concat(periods[test_start:test_end], ignore_index=True)

            # Выполнение шага
            oos_df, summary, study = self._optimize_step(step_num, train_slices, test_slices)

            if not oos_df.empty:
                all_oos_trades.append(oos_df)
            step_results.append(summary)
            last_study = study

        # Финализация
        logger.info("Оптимизация завершена. Генерация отчетов...")
        reporter = WFOReportGenerator(
            self.settings, all_oos_trades, step_results, last_study
        )
        reporter.generate()