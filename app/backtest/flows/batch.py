import os
import pandas as pd
import logging
from tqdm import tqdm
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any

from app.backtest.flows.single import _run_and_analyze_single_instrument

from app.analyzers.reports.excel_report import ExcelReportGenerator
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS
from config import PATH_CONFIG, DATA_FILE_EXTENSION, BACKTEST_CONFIG

logger = logging.getLogger(__name__)


def run_batch_backtest_flow(settings: Dict[str, Any]):
    """
    Основная функция-оркестратор для запуска пакетного бэктеста.
    Использует _run_and_analyze_single_instrument для параллельной обработки инструментов.
    """
    strategy_name = settings["strategy"]
    exchange = settings["exchange"]
    interval = settings["interval"]
    risk_manager_type = settings["risk_manager_type"]

    logger.info(f"--- Запуск потока пакетного тестирования для стратегии '{strategy_name}' ---")
    logger.info(f"Биржа: {exchange}, Интервал: {interval}, Риск-менеджер: {risk_manager_type}")

    interval_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval)
    if not os.path.isdir(interval_path):
        logger.error(f"Ошибка: Директория с данными не найдена: {interval_path}")
        return

    data_files = [f for f in os.listdir(interval_path) if f.endswith(DATA_FILE_EXTENSION)]
    if not data_files:
        logger.warning(f"В директории {interval_path} не найдено файлов данных ({DATA_FILE_EXTENSION}).")
        return
    logger.info(f"Найдено {len(data_files)} инструментов для тестирования.")

    strategy_class = AVAILABLE_STRATEGIES[strategy_name]
    rm_class = AVAILABLE_RISK_MANAGERS[risk_manager_type]
    strategy_params = strategy_class.get_default_params()
    rm_params = rm_class.get_default_params()

    logger.info(f"Используются параметры стратегии по умолчанию: {strategy_params}")
    logger.info(f"Используются параметры риск-менеджера по умолчанию: {rm_params}")

    # --- Подготовка задач для пула потоков ---
    tasks = []
    for filename in data_files:
        instrument = os.path.splitext(filename)[0]
        # Собираем полный словарь настроек для каждого инструмента
        task_settings = {
            "strategy_class": strategy_class,
            "exchange": exchange,
            "instrument": instrument,
            "interval": interval,
            "risk_manager_type": risk_manager_type,
            "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
            "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
            "data_dir": PATH_CONFIG["DATA_DIR"],
            "strategy_params": strategy_params,
            "risk_manager_params": rm_params,
            "trade_log_path": None,  # Не сохраняем индивидуальные логи сделок
        }
        tasks.append(task_settings)

    # --- Запуск многопоточного выполнения ---
    results_list = []
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        # В качестве функции для потока теперь выступает _run_and_analyze_single_instrument
        future_to_settings = {executor.submit(_run_and_analyze_single_instrument, task): task for task in tasks}

        progress_bar = tqdm(as_completed(future_to_settings), total=len(tasks), desc="Общий прогресс")
        for future in progress_bar:
            result_dict = future.result()
            if result_dict:
                # Добавляем имя инструмента, так как оно не возвращается из функции
                settings_for_future = future_to_settings[future]
                result_dict['instrument'] = settings_for_future['instrument']
                results_list.append(result_dict)

    if not results_list:
        logger.warning("Ни один из бэктестов не вернул корректных результатов.")
        return

    # --- Генерация итогового Excel-отчета ---
    results_df = pd.DataFrame(results_list)

    report_dir = PATH_CONFIG["REPORTS_BATCH_TEST_DIR"]
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