"""
Модуль запуска бэктестов (Runners).

Этот модуль содержит высокоуровневые функции-оркестраторы для выполнения
тестирования стратегий. Он связывает пользовательский ввод (CLI/GUI),
конфигурацию (`TradingConfig`) и ядро симуляции (`BacktestEngine`).

Основные функции:
    - **run_single_backtest_flow**: Детальный тест одной стратегии с генерацией
      полного отчета (графики, логи сделок).
    - **run_batch_backtest_flow**: Массовое тестирование на множестве инструментов
      с агрегацией результатов в Excel.
"""

import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from app.shared.schemas import TradingConfig
from app.core.engine.backtest.loop import BacktestEngine
from app.core.analysis.session import AnalysisSession
from app.core.analysis.reports.excel_report import ExcelReportGenerator
from app.shared.logging_setup import setup_backtest_logging, backtest_time_filter
from app.strategies import AVAILABLE_STRATEGIES
from app.shared.config import config as app_config

logger = logging.getLogger(__name__)


def _run_single_task(config: TradingConfig) -> Optional[Dict[str, Any]]:
    """
    Вспомогательная функция для выполнения одного теста в изолированном потоке.

    Используется в пакетном режиме (Batch Backtest). Не генерирует графики,
    возвращает только ключевые метрики для сводной таблицы.

    Args:
        config (TradingConfig): Конфигурация для конкретного инструмента.

    Returns:
        Optional[Dict[str, Any]]: Словарь с результатами (PnL, кол-во сделок)
        или None в случае ошибки.
    """
    try:
        # Создаем и запускаем движок
        engine = BacktestEngine(config=config)
        results = engine.run()

        if results["status"] == "success" and not results["trades_df"].empty:
            trades = results["trades_df"]

            # Базовый расчет метрик для Excel-отчета
            # (Детальный расчет Sharpe/Sortino происходит позже или может быть добавлен здесь)
            initial = results["initial_capital"]
            final = results["final_capital"]
            pnl_abs = final - initial
            pnl_pct = (pnl_abs / initial) * 100

            # Расчет Win Rate
            wins = trades[trades['pnl'] > 0]
            win_rate = len(wins) / len(trades) if len(trades) > 0 else 0.0

            # Расчет Max Drawdown (упрощенно по сделкам, для скорости)
            equity = initial + trades['pnl'].cumsum()
            peak = equity.cummax()
            dd = (equity - peak) / peak
            max_dd = abs(dd.min())

            return {
                "instrument": config.instrument,
                "pnl_abs": pnl_abs,
                "pnl_pct": pnl_pct,
                "total_trades": len(trades),
                "win_rate": win_rate,
                "max_drawdown": max_dd,
                # Считаем PnL Buy & Hold (нужны данные)
                "pnl_bh_pct": 0.0, # Можно доработать, если передавать enriched_data
                "profit_factor": 0.0 # Можно доработать через MetricsCalculator
            }

    except Exception as e:
        # Логируем ошибку, но не роняем весь батч
        # Используем print, т.к. логгер может быть настроен на tqdm
        pass

    return None


def run_single_backtest_flow(run_settings: Dict[str, Any]) -> None:
    """
    Запускает одиночный бэктест с полным логированием и отчетами.

    Алгоритм:
    1. Формирует `TradingConfig` из аргументов.
    2. Настраивает файловое логирование.
    3. Запускает симуляцию.
    4. Генерирует графические отчеты (AnalysisSession).

    Args:
        run_settings (Dict[str, Any]): Словарь настроек из CLI/Launcher.
    """
    strategy_name = run_settings["strategy"]
    strategy_cls = AVAILABLE_STRATEGIES[strategy_name]

    # Получаем дефолтные параметры стратегии
    strategy_params = strategy_cls.get_default_params()

    # Формируем конфиг риска
    risk_config = {
        "type": run_settings.get("risk_manager_type", "FIXED"),
        # Сюда можно добавить параметры из run_settings, если они там есть
    }

    # Создаем единый объект конфигурации
    config = TradingConfig(
        mode="BACKTEST",
        exchange=run_settings["exchange"],
        instrument=run_settings["instrument"],
        interval=run_settings["interval"],
        strategy_name=strategy_name,
        strategy_params=strategy_params,
        risk_config=risk_config,
        initial_capital=app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
        commission_rate=app_config.BACKTEST_CONFIG["COMMISSION_RATE"]
    )

    # Настройка путей для логов
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{timestamp}_{strategy_name}_{config.instrument}_{config.interval}"
    log_file = os.path.join(app_config.PATH_CONFIG["LOGS_BACKTEST_DIR"], f"{base_filename}.log")

    # Включаем детальное логирование в файл
    setup_backtest_logging(log_file)
    logger.info(f"Запуск одиночного бэктеста: {config}")

    # Запуск движка
    engine = BacktestEngine(config=config)
    results = engine.run()

    # Пост-процессинг и отчеты
    if results["status"] == "success" and not results["trades_df"].empty:
        logger.info(f"Бэктест завершен. Сделок: {len(results['trades_df'])}")

        # Инициализация сессии анализа
        session = AnalysisSession(
            trades_df=results["trades_df"],
            historical_data=results["enriched_data"],
            initial_capital=config.initial_capital,
            exchange=config.exchange,
            interval=config.interval,
            risk_manager_type=risk_config["type"],
            strategy_name=strategy_name
        )

        # Генерация графиков и консольного отчета
        session.generate_all_reports(
            base_filename=base_filename,
            report_dir=app_config.PATH_CONFIG["REPORTS_BACKTEST_DIR"]
        )

        # Сохранение лога сделок (JSONL) для Дашборда
        trades_log_path = os.path.join(app_config.PATH_CONFIG["LOGS_BACKTEST_DIR"], f"{base_filename}_trades.jsonl")
        results["trades_df"].to_json(trades_log_path, orient="records", lines=True, date_format="iso")
        logger.info(f"Лог сделок сохранен: {trades_log_path}")

    else:
        logger.warning("Бэктест завершен без сделок или возникла ошибка.")
        if results.get("message"):
            logger.error(f"Сообщение ошибки: {results['message']}")

    # Сброс фильтра времени в логгере
    backtest_time_filter.reset_sim_time()


def run_batch_backtest_flow(run_settings: Dict[str, Any]) -> None:
    """
    Запускает пакетное тестирование стратегии на списке инструментов.

    Алгоритм:
    1. Сканирует папку данных на наличие файлов `.parquet`.
    2. Создает `TradingConfig` для каждого инструмента.
    3. Запускает тесты параллельно через `ThreadPoolExecutor`.
    4. Агрегирует результаты в Excel-отчет.

    Args:
        run_settings (Dict[str, Any]): Настройки из CLI (без конкретного инструмента).
    """
    strategy_name = run_settings["strategy"]
    exchange = run_settings["exchange"]
    interval = run_settings["interval"]

    # Подготовка общих параметров
    strategy_cls = AVAILABLE_STRATEGIES[strategy_name]
    strategy_params = strategy_cls.get_default_params()
    risk_config = {"type": run_settings.get("risk_manager_type", "FIXED")}

    # Определение пути к данным
    data_dir = os.path.join(app_config.PATH_CONFIG["DATA_DIR"], exchange, interval)
    if not os.path.isdir(data_dir):
        logger.error(f"Директория с данными не найдена: {data_dir}")
        return

    # Поиск файлов
    files = [f for f in os.listdir(data_dir) if f.endswith(".parquet")]
    if not files:
        logger.warning("Нет данных для тестирования.")
        return

    logger.info(f"Найдено {len(files)} инструментов. Старт пакетного теста...")

    # Создание списка задач (Configs)
    configs = []
    for f in files:
        instrument = f.replace(".parquet", "")
        cfg = TradingConfig(
            mode="BACKTEST",
            exchange=exchange,
            instrument=instrument,
            interval=interval,
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            risk_config=risk_config,
            initial_capital=app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
            commission_rate=app_config.BACKTEST_CONFIG["COMMISSION_RATE"]
        )
        configs.append(cfg)

    # Параллельное выполнение
    results_list = []
    # Используем кол-во ядер CPU для воркеров
    max_workers = os.cpu_count() or 4

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit задач
        futures = {executor.submit(_run_single_task, cfg): cfg for cfg in configs}

        # Прогресс-бар
        for future in tqdm(as_completed(futures), total=len(configs), desc="Processing"):
            res = future.result()
            if res:
                results_list.append(res)

    if not results_list:
        logger.warning("Все тесты завершились без результатов.")
        return

    # Генерация Excel отчета
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"{timestamp}_BATCH_{strategy_name}_{interval}.xlsx"
    output_path = os.path.join(app_config.PATH_CONFIG["REPORTS_BATCH_TEST_DIR"], report_filename)

    # Убедимся, что папка существует
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        results_df = pd.DataFrame(results_list)

        generator = ExcelReportGenerator(
            results_df=results_df,
            strategy_name=strategy_name,
            interval=interval,
            risk_manager_type=risk_config["type"],
            strategy_params=strategy_params,
            rm_params=risk_config
        )
        generator.generate(output_path)
        logger.info(f"Пакетный тест завершен. Отчет: {output_path}")

    except Exception as e:
        logger.error(f"Ошибка при создании Excel-отчета: {e}", exc_info=True)