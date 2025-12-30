"""
Единый движок Walk-Forward Optimization (WFO).

Оптимизация стратегий методом скользящего окна.
Он управляет полным циклом: от загрузки и нарезки данных до запуска Optuna
и агрегации Out-of-Sample (тестовых) результатов.

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
from app.shared.factories import ConfigFactory

# Импорты инфраструктуры
from app.infrastructure.feeds.backtest.provider import BacktestDataLoader
from app.infrastructure.feeds.backtest.provider import EXCHANGE_SPECIFIC_CONFIG
from app.core.analysis.reports.wfo import WFOReportGenerator

logger = logging.getLogger(__name__)


class WFOEngine:
    """
    Оркестратор процесса Walk-Forward Optimization.

    Взаимодействием с Optuna и генерация итоговых отчетов.

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
        self.num_steps = 0
        self.strategy_cls = AVAILABLE_STRATEGIES[self.settings["strategy"]]
        self.risk_cls = RiskManager
        
        exchange_conf = EXCHANGE_SPECIFIC_CONFIG.get(self.settings["exchange"], {})
        self.annual_factor = exchange_conf.get("SHARPE_ANNUALIZATION_FACTOR", 252)

        # Флаг: Грузить всё в RAM или читать с диска по шагам?
        self.preload_data = self.settings.get("preload", False)
        
        # Кэш для режима Preload: {instrument: [df_period_1, df_period_2...]}
        self.preload_cache: Dict[str, List[pd.DataFrame]] = {}

    def _prepare_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        Валидация и донастройка параметров.

        Если передан путь к портфелю, сканирует папку на наличие файлов.
        Иначе использует одиночный инструмент.

        Args:
            settings (Dict[str, Any]): Исходные настройки.

        Returns:
            Dict[str, Any]: Обновленные настройки со списком инструментов.
        """
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

    def _validate_and_calc_wfo_steps(self):
        """
        Считает количество шагов WFO (легкая проверка одного файла).

        Загружает данные одного инструмента, чтобы определить, на сколько частей
        можно разбить историю и сколько шагов (num_steps) получится при
        заданных размерах окон Train/Test.

        Raises:
            ValueError: Если список инструментов пуст или данных недостаточно.
        """
        logger.info("Валидация данных и расчет шагов WFO...")
        
        if not self.settings["instrument_list"]:
            raise ValueError("Список инструментов пуст.")

        test_instr = self.settings["instrument_list"][0]
        loader = BacktestDataLoader(
            exchange=self.settings["exchange"],
            instrument_id=test_instr,
            interval_str=self.settings["interval"],
            data_path=app_config.PATH_CONFIG["DATA_DIR"]
        )
        
        # Грузим один раз, чтобы понять геометрию
        periods = loader.load_and_split(self.settings["total_periods"])
        if not periods:
            raise ValueError(f"Не удалось загрузить данные для {test_instr}")

        valid_periods_count = len(periods)
        self.num_steps = (
                valid_periods_count
                - self.settings["train_periods"]
                - self.settings["test_periods"] + 1
        )
        
        if self.num_steps <= 0:
            raise ValueError(f"Недостаточно периодов ({valid_periods_count}) для заданных окон.")

        mode_str = "RAM (High Performance)" if self.preload_data else "Disk (Low Memory)"
        logger.info(f"Режим: {mode_str}. Инструментов: {len(self.settings['instrument_list'])}. Шагов WFO: {self.num_steps}.")

    def _load_instrument_data_chunks(self, instr: str) -> Optional[List[pd.DataFrame]]:
        """
        Загрузка данных одного инструмента, разбитых на готовые части.
        
        Используется внутри ThreadPoolExecutor для параллельного чтения с диска.

        Args:
            instr (str): Тикер инструмента.

        Returns:
            Optional[List[pd.DataFrame]]: Список DataFrame (частей истории) или None при ошибке.
        """
        loader = BacktestDataLoader(
            exchange=self.settings["exchange"],
            instrument_id=instr,
            interval_str=self.settings["interval"],
            data_path=app_config.PATH_CONFIG["DATA_DIR"]
        )
        return loader.load_and_split(self.settings["total_periods"])
    
    def _preload_all_data(self):
        """
        Загружает ВСЕ данные в память (кэш) параллельно.
        
        Используется только если включен режим `preload`.
        Заполняет `self.preload_cache`.

        Raises:
            RuntimeError: Если не удалось загрузить данные ни для одного инструмента.
        """
        instruments = self.settings["instrument_list"]
        logger.info(f"Загрузка всех данных в RAM ({len(instruments)} инструментов)...")
        
        # Параллельная загрузка
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_instr = {executor.submit(self._load_instrument_data_chunks, instr): instr for instr in instruments}
            
            for future in tqdm(concurrent.futures.as_completed(future_to_instr), total=len(instruments), desc="Preloading"):
                instr = future_to_instr[future]
                try:
                    periods = future.result()
                    if periods:
                        self.preload_cache[instr] = periods
                except Exception as e:
                    logger.error(f"Ошибка загрузки {instr}: {e}")

        if not self.preload_cache:
            raise RuntimeError("Не удалось загрузить данные ни для одного инструмента.")


    def _generate_trial_params(self, trial: optuna.Trial) -> Tuple[Dict, Dict]:
        """
        Генерирует значения параметров для одной итерации обучения Optuna.

        Читает `params_config` из класса стратегии и риск-менеджера,
        создавая соответствующие `trial.suggest_int` или `trial.suggest_float`.

        Args:
            trial (optuna.Trial): Объект текущего испытания Optuna.

        Returns:
            Tuple[Dict, Dict]: Кортеж из двух словарей:
                - params стратегии
                - config риск-менеджера
        """
        # 1. Параметры Стратегии
        strat_params = {}
        strat_conf_map = {}

        # Собираем конфиги со всех родительских классов
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
        Запуск одного бэктеста в памяти.
        
        Используется в ThreadPoolExecutor внутри `_optuna_calc_objective_param`.

        Args:
            config (TradingConfig): Конфигурация сессии.
            data (pd.DataFrame): Срез исторических данных.

        Returns:
            pd.DataFrame: DataFrame сделок (пустой, если сделок не было или произошла ошибка).
        """
        engine = BacktestEngine(config=config, data_slice=data)
        res = engine.run()
        if res["status"] == "success":
            return res["trades_df"]
        return pd.DataFrame()

    def _optuna_calc_objective_param(self, trial: optuna.Trial, train_slices: Dict[str, pd.DataFrame]) -> Union[float, Tuple[float, ...]]:
        """
        Целевая функция оптимизации (In-Sample).

        1. Генерирует параметры через Optuna.
        2. Запускает параллельные бэктесты для всех инструментов в `train_slices`.
        3. Агрегирует результаты и считает метрику (например, Sharpe Ratio).

        Args:
            trial (optuna.Trial): Текущее испытание.
            train_slices (Dict[str, pd.DataFrame]): Данные для обучения.

        Returns:
            Union[float, Tuple[float, ...]]: Значение метрики (или кортеж для мульти-целевой оптимизации).
        """
        try:
            strat_params, risk_config = self._generate_trial_params(trial)
            base_config = ConfigFactory.create_trading_config(
                mode="OPTIMIZATION",
                exchange=self.settings["exchange"],
                instrument="TEMP",  # Будет подменяться в цикле
                interval=self.settings["interval"],
                strategy_name=self.settings["strategy"],
                strategy_params_override=strat_params,
                risk_config_override=risk_config
            )
            
            tasks = []
            for instr, data in train_slices.items():
                if not data.empty:
                    cfg = base_config.model_copy(update={"instrument": instr})
                    tasks.append((cfg, data))

            all_trades = []
            # Используем ThreadPool, т.к. pandas отпускает GIL
            with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
                futures = {executor.submit(self._run_single_backtest_memory, c, d): c for c, d in tasks}
                for f in concurrent.futures.as_completed(futures):
                    df = f.result()
                    if not df.empty:
                        all_trades.append(df)

            if not all_trades:
                raise optuna.TrialPruned("No trades generated.")

            portfolio_trades = pd.concat(all_trades, ignore_index=True)
            if 'exit_time' in portfolio_trades.columns:
                portfolio_trades['exit_time'] = pd.to_datetime(portfolio_trades['exit_time'])
                portfolio_trades.sort_values('exit_time', inplace=True)

            total_capital = base_config.initial_capital * len(train_slices)
            calc = PortfolioMetricsCalculator(portfolio_trades, total_capital, self.annual_factor)

            if not calc.is_valid:
                raise optuna.TrialPruned("Metrics calc failed.")

            metrics = calc.calculate_all()
            for k, v in metrics.items():
                trial.set_user_attr(k, v)

            targets = self.settings["metrics"]
            results = [metrics.get(m, -1e9) for m in targets]
            return results[0] if len(results) == 1 else tuple(results)

        except optuna.TrialPruned:
            raise
        except Exception:
            return tuple([-1e9] * len(self.settings["metrics"])) if len(self.settings["metrics"]) > 1 else -1e9

    def _optimize_step(self, step_num: int,
                       train_slices: Dict[str, pd.DataFrame],
                       test_slices: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, Dict, optuna.Study]:
        """
        Выполняет один шаг WFO (Train + Test).

        1. In-Sample: Запускает Optuna для поиска лучших параметров на `train_slices`.
        2. Out-of-Sample (OOS): Прогоняет лучшие параметры на `test_slices`.

        Args:
            step_num (int): Номер текущего шага (для логов).
            train_slices (Dict): Данные для обучения.
            test_slices (Dict): Данные для теста (будущее).

        Returns:
            Tuple:
                - real_execution_trades (pd.DataFrame): Сделки за OOS период.
                - step_summary (Dict): Информация о шаге и параметрах.
                - study (optuna.Study): Объект исследования Optuna.
        """
        tqdm.write(f"\n>>> WFO Шаг {step_num}/{self.num_steps}")

        directions = [METRIC_CONFIG[m]["direction"] for m in self.settings["metrics"]]
        study = optuna.create_study(directions=directions)

        study.optimize(
            lambda t: self._optuna_calc_objective_param(t, train_slices),
            n_trials=self.settings["n_trials"],
            n_jobs=1,
            show_progress_bar=True
        )

        if not study.best_trials:
            logger.warning(f"Шаг {step_num}: Не найдено успешных триалов.")
            return pd.DataFrame(), {"status": "FAILED"}, study

        best_trial = study.best_trials[0]
        if len(study.directions) > 1:
            best_trial = max(study.best_trials, key=lambda t: t.values[0])

        # 3. Подготовка параметров для OOS теста
        best_params = best_trial.params
        
        # Разделяем параметры
        strat_params_override = {k: v for k, v in best_params.items() if not k.startswith("rm_")}
        risk_params_override = {k[3:]: v for k, v in best_params.items() if k.startswith("rm_")}

        # Собираем риск конфиг
        final_risk_config = {**{"type": self.settings["rm"]}, **risk_params_override}

        # 4. Запуск Out-of-Sample (Test) через Фабрику
        # Фабрика сама возьмет дефолты стратегии и наложит сверху strat_params_override.
        base_config = ConfigFactory.create_trading_config(
            mode="OPTIMIZATION",
            exchange=self.settings["exchange"],
            instrument="TEMP",
            interval=self.settings["interval"],
            strategy_name=self.settings["strategy"],
            strategy_params_override=strat_params_override,
            risk_config_override=final_risk_config
        )

        all_oos_trades = []
        # OOS тест быстрый, можно в одном потоке, но для портфеля лучше параллельно
        # Если портфель большой, можно ускорить и тут
        for instr, data in test_slices.items():
            if not data.empty:
                cfg = base_config.model_copy(update={"instrument": instr})
                engine = BacktestEngine(config=cfg, data_slice=data)
                res = engine.run()
                if res["status"] == "success" and not res["trades_df"].empty:
                    all_oos_trades.append(res["trades_df"])

        real_execution_trades = pd.concat(all_oos_trades, ignore_index=True) if all_oos_trades else pd.DataFrame()

        step_summary = {
            "step": step_num,
            "train_start_idx": list(train_slices.values())[0]['time'].iloc[0],
            "test_start_idx": list(test_slices.values())[0]['time'].iloc[0],
            **best_trial.params,
            **best_trial.user_attrs
        }

        tqdm.write(f"Шаг {step_num} завершен. Сделок на OOS: {len(real_execution_trades)}")
        return real_execution_trades, step_summary, study

    def run(self):
        """
        Запускает полный процесс оптимизации.

        1. Рассчитывает количество шагов.
        2. (Опционально) Загружает все данные в RAM.
        3. Запускает цикл по шагам (сдвиг окна).
        4. В каждом шаге формирует Train/Test выборки.
        5. Агрегирует результаты и генерирует отчеты.
        """
        self._validate_and_calc_wfo_steps()

        all_oos_trades = []
        step_results = []
        last_study = None

        for step_num in range(1, self.num_steps + 1):
            # Расчет номеров окон
            train_start = step_num - 1
            train_end = train_start + self.settings["train_periods"]
            test_start = train_end
            test_end = test_start + self.settings["test_periods"]
            
            tqdm.write(f"\n>>> Подготовка данных для шага {step_num}/{self.num_steps}...")

            train_slices = {}
            test_slices = {}
            instruments = self.settings["instrument_list"]
            
            # RAM (Fast)
            if self.preload_data:
                # Если включен режим Preload - грузим всё сразу
                self._preload_all_data()
                for instr in instruments:
                    periods = self.preload_cache.get(instr)
                    if periods and len(periods) >= test_end:
                        train_slices[instr] = pd.concat(periods[train_start:train_end], ignore_index=True)
                        test_slices[instr] = pd.concat(periods[test_start:test_end], ignore_index=True)
            # Disk (Low RAM)
            else:
                # Параллельная загрузка с диска для ускорения
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future_to_instr = {executor.submit(self._load_instrument_data_chunks, instr): instr for instr in instruments}
                    
                    for future in tqdm(concurrent.futures.as_completed(future_to_instr), total=len(instruments), desc=f"Loading Step {step_num}", leave=False):
                        instr = future_to_instr[future]
                        try:
                            periods = future.result()
                            if periods and len(periods) >= test_end:
                                train_slices[instr] = pd.concat(periods[train_start:train_end], ignore_index=True)
                                test_slices[instr] = pd.concat(periods[test_start:test_end], ignore_index=True)
                        except Exception as e:
                            logger.error(f"Ошибка чтения {instr}: {e}")

            if not train_slices:
                logger.error("Нет данных для обучения на этом шаге. Прерывание.")
                break

            # Выполнение шага
            real_execution_trades, summary, study = self._optimize_step(step_num, train_slices, test_slices)

            # Очистка локальных переменных шага
            del train_slices
            del test_slices

            if not real_execution_trades.empty:
                all_oos_trades.append(real_execution_trades)
            step_results.append(summary)
            last_study = study

        logger.info("Оптимизация завершена. Генерация отчетов...")
        reporter = WFOReportGenerator(
            self.settings, all_oos_trades, step_results, last_study
        )
        reporter.generate()