"""
Модуль запуска бэктестов (Runners).

Содержит функции-оркестраторы ("flows"), которые управляют полным циклом тестирования:
от инициализации настроек до генерации итоговых отчетов.

Основные сценарии:
1.  **Single Backtest**: Детальный тест одной стратегии на одном инструменте с
    полным логированием каждой сделки и генерацией графиков.
2.  **Batch Backtest**: Массовое тестирование стратегии на списке инструментов
    (например, топ-10 монет) с агрегацией результатов в Excel.
"""

import logging
import os
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd
from tqdm import tqdm

from app.core.analysis.metrics import PortfolioMetricsCalculator, BenchmarkMetricsCalculator
from app.core.analysis.reports.excel_report import ExcelReportGenerator
from app.core.analysis.session import AnalysisSession
from app.core.engine.backtest.loop import BacktestEngine
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.shared.logging_setup import setup_backtest_logging, backtest_time_filter
from app.strategies import AVAILABLE_STRATEGIES
from app.shared.config import config
from app.bootstrap.container import container

logger = logging.getLogger(__name__)


def _run_and_analyze_single_instrument(engine_settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Рабочая функция для запуска бэктеста на одном инструменте.

    Используется как в одиночном, так и в пакетном режиме (и в оптимизации).
    Выполняет симуляцию, считает базовые метрики и возвращает результат
    без генерации графических отчетов (это задача вызывающего кода).

    Args:
        engine_settings (Dict): Полная конфигурация для BacktestEngine.

    Returns:
        Optional[Dict]: Словарь с результатами (сделки, метрики, капитал) или None в случае ошибки.
    """
    try:
        events_queue = queue.Queue()
        # Используем FeatureEngine из DI-контейнера для оптимизации ресурсов
        feature_engine = container.feature_engine

        engine = BacktestEngine(
            settings=engine_settings,
            events_queue=events_queue,
            feature_engine=feature_engine
        )
        results = engine.run()

        if results["status"] == "success" and not results["trades_df"].empty:
            exchange = engine_settings["exchange"]
            annual_factor = config.EXCHANGE_SPECIFIC_CONFIG[exchange]["SHARPE_ANNUALIZATION_FACTOR"]

            # 1. Расчет метрик стратегии
            portfolio_calc = PortfolioMetricsCalculator(
                trades_df=results["trades_df"],
                initial_capital=results["initial_capital"],
                annualization_factor=annual_factor
            )
            portfolio_metrics = portfolio_calc.calculate_all()

            # 2. Расчет метрик бенчмарка (Buy & Hold)
            bench_calc = BenchmarkMetricsCalculator(
                historical_data=results["enriched_data"],
                initial_capital=results["initial_capital"],
                annualization_factor=annual_factor
            )
            bench_metrics = bench_calc.calculate_all()

            # 3. Сборка итогового словаря
            full_metrics = {
                **portfolio_metrics,
                'pnl_bh_pct': bench_metrics.get('pnl_pct', 0.0),
                "trades_df": results["trades_df"],
                "enriched_data": results["enriched_data"],
                "initial_capital": results["initial_capital"]
            }
            return full_metrics

    except Exception as e:
        instrument = engine_settings.get("instrument", "N/A")
        logger.error(f"Ошибка при обработке инструмента '{instrument}': {e}", exc_info=True)

    return None


def run_single_backtest_flow(run_settings: Dict[str, Any]):
    """
    Сценарий запуска одиночного бэктеста.

    Особенности:
    - Включает детальное логирование в файл.
    - Генерирует полные отчеты (графики, консольный вывод).
    - Предназначен для глубокого анализа работы стратегии.

    Args:
        run_settings (Dict): Параметры запуска из CLI/Launcher.
    """
    strategy_name = run_settings["strategy"]
    instrument = run_settings["instrument"]
    interval = run_settings["interval"]
    risk_manager_type = run_settings["risk_manager_type"]
    exchange = run_settings["exchange"]

    strategy_class = AVAILABLE_STRATEGIES[strategy_name]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Формируем имя файла лога и отчета
    base_filename = (
        f"{timestamp}_{strategy_class.__name__}_{instrument}_"
        f"{interval}_RM-{risk_manager_type}"
    )

    log_dir = config.PATH_CONFIG["LOGS_BACKTEST_DIR"]
    log_file_path = os.path.join(log_dir, f"{base_filename}_run.log")
    trade_log_path = os.path.join(log_dir, f"{base_filename}_trades.jsonl")

    # Настраиваем отдельный логгер для этого прогона
    setup_backtest_logging(log_file_path)

    logger.info(f"Запуск потока одиночного бэктеста: {base_filename}")

    engine_settings = {
        "strategy_class": strategy_class,
        "exchange": exchange,
        "instrument": instrument,
        "interval": interval,
        "risk_manager_type": risk_manager_type,
        "initial_capital": config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
        "commission_rate": config.BACKTEST_CONFIG["COMMISSION_RATE"],
        "data_dir": config.PATH_CONFIG["DATA_DIR"],
        "trade_log_path": trade_log_path,
        "strategy_params": None,  # Используются дефолтные
        "risk_manager_params": None
    }

    try:
        analysis_results = _run_and_analyze_single_instrument(engine_settings)

        if analysis_results:
            logger.info(f"Бэктест завершен, найдено {analysis_results['total_trades']} сделок. Генерация отчетов.")

            # Запуск сессии анализа для генерации графиков
            analysis_session = AnalysisSession(
                trades_df=analysis_results["trades_df"],
                historical_data=analysis_results["enriched_data"],
                initial_capital=analysis_results["initial_capital"],
                exchange=exchange,
                interval=interval,
                risk_manager_type=risk_manager_type,
                strategy_name=strategy_class.__name__
            )

            analysis_session.generate_all_reports(
                base_filename=base_filename,
                report_dir=config.PATH_CONFIG["REPORTS_BACKTEST_DIR"]
            )
        else:
            logger.warning("Бэктест завершен без сделок или с ошибкой. Отчеты не сгенерированы.")

    except Exception:
        logger.critical("Произошла критическая ошибка во время выполнения потока бэктеста!", exc_info=True)
    finally:
        backtest_time_filter.reset_sim_time()
        logger.info("--- Поток одиночного бэктеста завершен ---")


def run_batch_backtest_flow(run_settings: Dict[str, Any]):
    """
    Сценарий запуска пакетного бэктеста.

    Особенности:
    - Запускает тесты параллельно (multiprocessing/threading).
    - Не генерирует графики для каждого инструмента (слишком много файлов).
    - Собирает сводную статистику в один Excel-файл.

    Args:
        run_settings (Dict): Параметры запуска.
    """
    strategy_name = run_settings["strategy"]
    exchange = run_settings["exchange"]
    interval = run_settings["interval"]
    risk_manager_type = run_settings["risk_manager_type"]

    logger.info(f"--- Запуск потока пакетного тестирования для стратегии '{strategy_name}' ---")
    logger.info(f"Биржа: {exchange}, Интервал: {interval}, Риск-менеджер: {risk_manager_type}")

    interval_path = os.path.join(config.PATH_CONFIG["DATA_DIR"], exchange, interval)
    if not os.path.isdir(interval_path):
        logger.error(f"Ошибка: Директория с данными не найдена: {interval_path}")
        return

    # Сканируем папку на наличие файлов данных
    data_files = [f for f in os.listdir(interval_path) if f.endswith(config.DATA_FILE_EXTENSION)]
    if not data_files:
        logger.warning(f"В директории {interval_path} не найдено файлов данных ({config.DATA_FILE_EXTENSION}).")
        return
    logger.info(f"Найдено {len(data_files)} инструментов для тестирования.")

    strategy_class = AVAILABLE_STRATEGIES[strategy_name]
    rm_class = AVAILABLE_RISK_MANAGERS[risk_manager_type]
    strategy_params = strategy_class.get_default_params()
    rm_params = rm_class.get_default_params()

    logger.info(f"Используются параметры стратегии по умолчанию: {strategy_params}")
    logger.info(f"Используются параметры риск-менеджера по умолчанию: {rm_params}")

    # Формируем список задач
    tasks = []
    for filename in data_files:
        instrument = os.path.splitext(filename)[0]
        task_settings = {
            "strategy_class": strategy_class,
            "exchange": exchange,
            "instrument": instrument,
            "interval": interval,
            "risk_manager_type": risk_manager_type,
            "initial_capital": config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
            "commission_rate": config.BACKTEST_CONFIG["COMMISSION_RATE"],
            "data_dir": config.PATH_CONFIG["DATA_DIR"],
            "strategy_params": strategy_params,
            "risk_manager_params": rm_params,
            "trade_log_path": None,  # В пакетном режиме детальные логи сделок отключены для экономии места
        }
        tasks.append(task_settings)

    # Параллельный запуск
    results_list = []
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        future_to_settings = {executor.submit(_run_and_analyze_single_instrument, task): task for task in tasks}

        # Используем tqdm для отображения прогресса по всем задачам
        progress_bar = tqdm(as_completed(future_to_settings), total=len(tasks), desc="Общий прогресс")
        for future in progress_bar:
            result_dict = future.result()
            if result_dict:
                settings_for_future = future_to_settings[future]
                result_dict['instrument'] = settings_for_future['instrument']
                results_list.append(result_dict)

    if not results_list:
        logger.warning("Ни один из бэктестов не вернул корректных результатов.")
        return

    # Генерация сводного Excel-отчета
    results_df = pd.DataFrame(results_list)
    report_dir = config.PATH_CONFIG["REPORTS_BATCH_TEST_DIR"]
    os.makedirs(report_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"{timestamp}_{strategy_name}_{interval}_{len(results_df)}instr.xlsx"
    output_path = os.path.join(report_dir, report_filename)

    excel_generator = ExcelReportGenerator(
        results_df=results_df,
        strategy_name=strategy_name,
        interval=interval,
        risk_manager_type=risk_manager_type,
        strategy_params=strategy_params,
        rm_params=rm_params
    )
    excel_generator.generate(output_path)
    logger.info(f"\n--- Поток пакетного тестирования успешно завершен. Отчет сохранен в {output_path} ---")