"""
Единый движок Walk-Forward Optimization (WFO).

Этот модуль объединяет в себе всю логику оптимизации:
1. Инициализация и подготовка данных (через BacktestDataLoader).
2. Основной цикл WFO (пошаговый сдвиг окна).
3. Оптимизация на шаге (Optuna Study + Objective Function).
4. Тестирование на OOS данных.
5. Запуск генерации отчетов.
"""

import os
import logging
import concurrent.futures
from typing import Dict, Any, List, Tuple, Optional, Union

import pandas as pd
import numpy as np
import optuna
from tqdm import tqdm

import app.infrastructure.feeds.backtest.provider
# Импорты ядра
from app.core.engine.backtest.loop import BacktestEngine
from app.core.analysis.metrics import PortfolioMetricsCalculator
from app.core.analysis.constants import METRIC_CONFIG
from app.core.risk import RiskManager
from app.strategies import AVAILABLE_STRATEGIES
from app.shared.schemas import TradingConfig
from app.shared.config import config as app_config

# Импорты инфраструктуры и отчетов
from app.infrastructure.feeds.backtest.provider import BacktestDataLoader
from app.core.analysis.reports.wfo_report import WFOReportGenerator

logger = logging.getLogger(__name__)


class WFOOptimizer:
    """
    Оркестратор WFO.

    Управляет полным циклом: от загрузки данных до генерации HTML-отчета.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Args:
            settings: Словарь конфигурации из CLI (exchange, strategy, periods, etc).
        """
        self.settings = self._prepare_settings(settings)

        # Кэш данных: {instrument: [df_period_1, df_period_2, ...]}
        self.all_instrument_periods: Dict[str, List[pd.DataFrame]] = {}
        self.num_steps = 0

        # Ссылки на классы стратегии и риска для интроспекции параметров
        self.strategy_cls = AVAILABLE_STRATEGIES[self.settings["strategy"]]
        self.risk_cls = RiskManager

        # Коэффициент аннуализации (константа для Шарпа)
        self.annual_factor = app.infrastructure.feeds.backtest.provider.EXCHANGE_SPECIFIC_CONFIG.get(
            self.settings["exchange"], {}
        ).get("SHARPE_ANNUALIZATION_FACTOR", 252)

    def _prepare_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Валидация и донастройка параметров."""
        # Определение списка инструментов
        if settings.get("portfolio_path"):
            path = settings["portfolio_path"]
            if os.path.exists(path):
                settings["instrument_list"] = sorted(
                    [f.replace('.parquet', '') for f in os.listdir(path) if f.endswith('.parquet')]
                )
            else:
                logger.error(f"Portfolio path not found: {path}")
                settings["instrument_list"] = []
        else:
            settings["instrument_list"] = [settings["instrument"]]

        return settings

    def _load_data(self):
        """Загружает данные и режет их на периоды (используя новый BacktestDataLoader)."""
        logger.info("Загрузка и нарезка данных...")

        valid_periods_count = None

        for instr in tqdm(self.settings["instrument_list"], desc="Loading Data"):
            loader = BacktestDataLoader(
                exchange=self.settings["exchange"],
                instrument_id=instr,
                interval_str=self.settings["interval"],
                data_path=app_config.PATH_CONFIG["DATA_DIR"]
            )

            periods = loader.load_and_split(self.settings["total_periods"])

            if periods:
                self.all_instrument_periods[instr] = periods
                # Проверка целостности: у всех инструментов должно быть одинаковое кол-во кусков
                if valid_periods_count is None:
                    valid_periods_count = len(periods)
                elif len(periods) != valid_periods_count:
                    logger.warning(
                        f"Инструмент {instr} имеет {len(periods)} частей, ожидалось {valid_periods_count}. Пропуск.")
                    del self.all_instrument_periods[instr]

        if not self.all_instrument_periods:
            raise FileNotFoundError("Не удалось загрузить валидные данные ни для одного инструмента.")

        # Расчет количества шагов WFO
        # Total = Train + Test + (Steps - 1) -> Steps = Total - Train - Test + 1
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
        """Генерирует параметры для Optuna на основе конфигов классов."""
        # 1. Стратегия
        strat_params = {}
        strat_conf_map = {}
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
                strat_params[name] = conf.get("default")

        # 2. Риск-менеджер
        risk_params = {"type": self.settings["rm"]}  # Тип фиксирован при запуске

        if hasattr(self.risk_cls, 'params_config'):
            risk_conf_map = {}
            for base in reversed(self.risk_cls.__mro__):
                if hasattr(base, 'params_config'):
                    risk_conf_map.update(base.params_config)

            for name, conf in risk_conf_map.items():
                if conf.get("optimizable", False):
                    optuna_name = f"rm_{name}"  # Префикс для уникальности
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
        """Статический метод для запуска в ThreadPool."""
        engine = BacktestEngine(config=config, data_slice=data)
        res = engine.run()
        if res["status"] == "success":
            return res["trades_df"]
        return pd.DataFrame()

    def _objective(self, trial: optuna.Trial, train_slices: Dict[str, pd.DataFrame]) -> Union[float, Tuple[float, ...]]:
        """Фитнес-функция для Optuna."""
        try:
            # 1. Генерация параметров
            strat_params, risk_config = self._suggest_params(trial)

            # 2. Конфиг
            base_config = TradingConfig(
                mode="OPTIMIZATION",
                exchange=self.settings["exchange"],
                instrument="TEMP",
                interval=self.settings["interval"],
                strategy_name=self.settings["strategy"],
                strategy_params=strat_params,
                risk_config=risk_config,
                initial_capital=app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
                commission_rate=app_config.BACKTEST_CONFIG["COMMISSION_RATE"]
            )

            # 3. Параллельный запуск бэктестов
            tasks = []
            for instr, data in train_slices.items():
                if not data.empty:
                    cfg = base_config.model_copy(update={"instrument": instr})
                    tasks.append((cfg, data))

            all_trades = []
            # Используем ThreadPoolExecutor для ускорения (IO-bound нет, но есть GIL release в numpy/pandas)
            with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
                futures = {executor.submit(self._run_single_backtest_memory, c, d): c for c, d in tasks}
                for f in concurrent.futures.as_completed(futures):
                    df = f.result()
                    if not df.empty:
                        all_trades.append(df)

            if not all_trades:
                raise optuna.TrialPruned("No trades.")

            # 4. Расчет метрик
            portfolio_trades = pd.concat(all_trades, ignore_index=True)
            if 'exit_timestamp_utc' in portfolio_trades.columns:
                portfolio_trades['exit_timestamp_utc'] = pd.to_datetime(portfolio_trades['exit_timestamp_utc'])
                portfolio_trades.sort_values('exit_timestamp_utc', inplace=True)

            total_capital = base_config.initial_capital * len(train_slices)
            calc = PortfolioMetricsCalculator(portfolio_trades, total_capital, self.annual_factor)

            if not calc.is_valid:
                raise optuna.TrialPruned("Invalid metrics.")

            metrics = calc.calculate_all()

            # Сохраняем метрики в Trial
            for k, v in metrics.items():
                trial.set_user_attr(k, v)

            # Возврат целевых значений
            targets = self.settings["metrics"]
            results = []
            for m in targets:
                val = metrics.get(m, -1e9)  # Fallback для maximize
                results.append(val)

            return results[0] if len(results) == 1 else tuple(results)

        except optuna.TrialPruned:
            raise
        except Exception:
            # Возвращаем "плохие" значения при ошибках
            return tuple([-1e9] * len(self.settings["metrics"])) if len(self.settings["metrics"]) > 1 else -1e9

    # --- LOGIC: STEP RUNNER ---

    def _optimize_step(self, step_num: int,
                       train_slices: Dict[str, pd.DataFrame],
                       test_slices: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, Dict, optuna.Study]:
        """Выполняет один шаг WFO (In-Sample Optimize -> Out-of-Sample Test)."""
        tqdm.write(f"\n>>> Шаг {step_num}/{self.num_steps}")

        # 1. In-Sample Optimization
        directions = [METRIC_CONFIG[m]["direction"] for m in self.settings["metrics"]]
        study = optuna.create_study(directions=directions)

        # Запуск оптимизации (передаем train_slices через lambda)
        study.optimize(
            lambda t: self._objective(t, train_slices),
            n_trials=self.settings["n_trials"],
            n_jobs=1,  # Внутри objective уже есть параллелизм, тут ставим 1
            show_progress_bar=True
        )

        # 2. Выбор лучшего Trial
        if not study.best_trials:
            return pd.DataFrame(), {"status": "FAILED"}, study

        # Логика выбора лучшего (если мульти-критерий, берем Calmar или первый)
        best_trial = study.best_trials[0]
        if len(study.directions) > 1:
            # Сортировка Парето-фронта по первой метрике
            best_trial = max(study.best_trials, key=lambda t: t.values[0])

        # 3. Out-of-Sample Test (Validation)
        # Извлекаем параметры (очистка от префиксов rm_)
        best_params = best_trial.params
        strat_params = {k: v for k, v in best_params.items() if not k.startswith("rm_")}
        risk_params = {k[3:]: v for k, v in best_params.items() if k.startswith("rm_")}

        # Слияние с дефолтами
        final_strat = {**self.strategy_cls.get_default_params(), **strat_params}
        final_risk = {**{"type": self.settings["rm"]},
                      **risk_params}  # Risk params могут быть пустыми, если нет optimization

        # Запуск теста на OOS данных
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
        # Последовательный запуск OOS (быстрее, чем поднимать пул на 1 секунду)
        for instr, data in test_slices.items():
            if not data.empty:
                cfg = base_config.model_copy(update={"instrument": instr})
                engine = BacktestEngine(config=cfg, data_slice=data)
                res = engine.run()
                if res["status"] == "success" and not res["trades_df"].empty:
                    all_oos_trades.append(res["trades_df"])

        oos_df = pd.concat(all_oos_trades, ignore_index=True) if all_oos_trades else pd.DataFrame()

        step_summary = {
            "step": step_num,
            "train_start": list(train_slices.values())[0]['time'].iloc[0],  # Для логов
            "test_start": list(test_slices.values())[0]['time'].iloc[0],
            **best_trial.params,
            **best_trial.user_attrs
        }

        tqdm.write(f"Шаг {step_num} завершен. OOS сделок: {len(oos_df)}")
        return oos_df, step_summary, study

    # --- MAIN RUN ---

    def run(self):
        """Запускает полный процесс оптимизации."""
        self._load_data()

        all_oos_trades = []
        step_results = []
        last_study = None

        # Цикл по шагам (Rolling Window)
        for step_num in range(1, self.num_steps + 1):
            # Индексы периодов
            train_start = step_num - 1
            train_end = train_start + self.settings["train_periods"]
            test_start = train_end
            test_end = test_start + self.settings["test_periods"]

            # Сборка слайсов данных
            train_slices = {}
            test_slices = {}

            for instr, periods in self.all_instrument_periods.items():
                # pd.concat склеивает список периодов (DataFrame)
                train_slices[instr] = pd.concat(periods[train_start:train_end], ignore_index=True)
                test_slices[instr] = pd.concat(periods[test_start:test_end], ignore_index=True)

            # Запуск шага
            oos_df, summary, study = self._optimize_step(step_num, train_slices, test_slices)

            if not oos_df.empty:
                all_oos_trades.append(oos_df)
            step_results.append(summary)
            last_study = study

        # Генерация отчетов
        logger.info("Оптимизация завершена. Генерация отчетов...")
        reporter = WFOReportGenerator(
            self.settings, all_oos_trades, step_results, last_study
        )
        reporter.generate()