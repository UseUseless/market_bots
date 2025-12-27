"""
Модуль запуска бэктестов.

Он оркестрирует процесс выполнения:
1. Подготовка конфигурации (Merge параметров).
2. Инициализация и запуск движка (`BacktestEngine`).
3. Сбор результатов и передача их в модуль аналитики/отчетности.

Основные функции:
    - run_single_backtest_flow: Одиночный запуск с детальными отчетами.
    - run_batch_backtest_flow: Массовый запуск с агрегацией в Excel.
"""

import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tqdm import tqdm

from app.shared.schemas import TradingConfig, RunModeType
from app.core.engine.backtest.engine import BacktestEngine
from app.core.analysis.session import AnalysisSession
from app.core.analysis.reports.excel import ExcelReportGenerator
from app.shared.logging_setup import setup_backtest_logging, backtest_time_filter
from app.strategies import AVAILABLE_STRATEGIES
from app.shared.config import config as app_config

logger = logging.getLogger(__name__)


def _create_config(run_settings: Dict[str, Any], mode: RunModeType) -> TradingConfig:
    """
    Сборка единого конфига.

    Обеъдиняет в один объект параметры:
    1. Параметры из стратегии.
    2. Параметры из консоли (CLI).

    Args:
        run_settings: Словарь аргументов из командной строки.
        mode: Режим запуска ('BACKTEST').

    Returns:
        TradingConfig: Готовый валидированный объект конфигурации.
    """
    strategy_name = run_settings["strategy"]

    # 1. Получение класса стратегии для доступа к дефолтным параметрам
    strategy_cls = AVAILABLE_STRATEGIES.get(strategy_name)
    if not strategy_cls:
        raise ValueError(f"Стратегия '{strategy_name}' не найдена в реестре.")

    # 2. Сборка параметров стратегии (Defaults | CLI Overrides)
    final_strategy_params = strategy_cls.get_default_params()

    # Если в run_settings будут переданы специфичные параметры (например из оптимизатора),
    # обновляем их здесь:
    if "strategy_params" in run_settings:
        final_strategy_params.update(run_settings["strategy_params"])

    # 3. Получение параметров риска
    risk_config = {
        "type": run_settings.get("risk_manager_type", "FIXED")
    }

    # 4. Создание DTO
    return TradingConfig(
        mode=mode,
        exchange=run_settings["exchange"],
        instrument=run_settings["instrument"],
        interval=run_settings["interval"],
        strategy_name=strategy_name,
        strategy_params=final_strategy_params,
        risk_config=risk_config,
        initial_capital=app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
        commission_rate=app_config.BACKTEST_CONFIG["COMMISSION_RATE"],
        slippage_config=app_config.BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {})
    )


def _run_single_batch_task(config: TradingConfig) -> Optional[Dict[str, Any]]:
    """
    Воркер для выполнения одного теста в изолированном потоке (для Batch режима).

    Не генерирует графики, возвращает только ключевые метрики для сводной таблицы.
    """
    try:
        # Инициализация движка с готовым конфигом
        engine = BacktestEngine(config=config)
        results = engine.run()

        if results["status"] == "success" and not results["trades_df"].empty:
            trades = results["trades_df"]
            initial = results["initial_capital"]
            final = results["final_capital"]

            # Быстрый расчет метрик для сводки (без AnalysisSession)
            # Можно и его добавить для более объемной аналитики
            pnl_abs = final - initial
            pnl_pct = (pnl_abs / initial) * 100

            equity = initial + trades['pnl'].cumsum()
            peak = equity.cummax()
            dd = (equity - peak) / peak
            max_dd = abs(dd.min())

            return {
                "instrument": config.instrument,
                "pnl_abs": pnl_abs,
                "pnl_pct": pnl_pct,
                "total_trades": len(trades),
                "win_rate": (trades['pnl'] > 0).mean(),
                "max_drawdown": max_dd,
                # Заглушки для полей, требующих полной истории (бенчмарк)
                "pnl_bh_pct": 0.0,
                "profit_factor": 0.0,
                "sharpe_ratio": 0.0
            }

    except Exception:
        # Ошибки в потоках подавляем, чтобы не прерывать батч, но логируем в вызывающем коде
        return None
    return None


def run_single_backtest_flow(run_settings: Dict[str, Any]) -> None:
    """
    Запускает полный цикл одиночного бэктеста.

    Flow:
    1. Сборка Config (CLI -> TradingConfig).
    2. Настройка логирования в файл.
    3. Запуск Engine.
    4. Запуск AnalysisSession (генерация отчетов).
    """

    # 1. Сборка конфигурации
    try:
        config = _create_config(run_settings, mode="BACKTEST")
    except ValueError as e:
        print(f"Ошибка конфигурации: {e}")
        return

    # 2. Настройка логов
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{timestamp}_{config.strategy_name}_{config.instrument}_{config.interval}"
    log_file = os.path.join(app_config.PATH_CONFIG["LOGS_BACKTEST_DIR"], f"{base_filename}.log")

    setup_backtest_logging(log_file)
    logger.info(f"Запуск бэктеста: {config.instrument} | {config.strategy_name}")

    # 3. Запуск Движка (Execution)
    engine = BacktestEngine(config=config)
    results = engine.run()

    # 4. Анализ и Отчетность (Reporting)
    if results["status"] == "success" and not results["trades_df"].empty:
        logger.info(f"Симуляция завершена. Сделок: {len(results['trades_df'])}")

        session = AnalysisSession(
            trades_df=results["trades_df"],
            historical_data=results["enriched_data"],
            initial_capital=config.initial_capital,
            exchange=config.exchange,
            interval=config.interval,
            risk_manager_type=config.risk_config["type"],
            strategy_name=config.strategy_name
        )

        session.generate_all_reports(
            base_filename=base_filename,
            report_dir=app_config.PATH_CONFIG["REPORTS_BACKTEST_DIR"]
        )

        # Сохранение сырых сделок для Дашборда
        trades_log_path = os.path.join(app_config.PATH_CONFIG["LOGS_BACKTEST_DIR"], f"{base_filename}_trades.jsonl")
        results["trades_df"].to_json(trades_log_path, orient="records", lines=True, date_format="iso")
        logger.info(f"Лог сделок сохранен: {trades_log_path}")

    else:
        logger.warning("Бэктест завершен без сделок или произошла ошибка.")
        if results.get("message"):
            logger.error(f"Reason: {results['message']}")

    backtest_time_filter.reset_sim_time()


def run_batch_backtest_flow(run_settings: Dict[str, Any]) -> None:
    """
    Запускает пакетное тестирование на папке с данными.

    Flow:
    1. Поиск файлов данных.
    2. Генерация списка конфигов для каждого файла.
    3. Параллельный запуск Engine.
    4. Агрегация результатов в Excel.
    """
    exchange = run_settings["exchange"]
    interval = run_settings["interval"]

    # 1. Сканирование данных
    data_dir = os.path.join(app_config.PATH_CONFIG["DATA_DIR"], exchange, interval)
    if not os.path.isdir(data_dir):
        logger.error(f"Папка с данными не найдена: {data_dir}")
        return

    files = [f for f in os.listdir(data_dir) if f.endswith(".parquet")]
    if not files:
        logger.warning("Нет данных (.parquet) для тестирования.")
        return

    logger.info(f"Старт пакетного теста: {len(files)} инструментов.")

    # 2. Подготовка задач (Config Assembly)
    configs = []
    base_settings = run_settings.copy() # Копируем, чтобы менять instrument

    for f in files:
        instrument = f.replace(".parquet", "")
        # Подменяем инструмент в настройках для сборщика
        base_settings["instrument"] = instrument
        try:
            cfg = _create_config(base_settings, mode="BACKTEST")
            configs.append(cfg)
        except ValueError:
            continue

    # 3. Параллельное выполнение
    results_list = []
    max_workers = os.cpu_count() or 4

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_single_batch_task, cfg): cfg for cfg in configs}

        for future in tqdm(as_completed(futures), total=len(configs), desc="Processing"):
            res = future.result()
            if res:
                results_list.append(res)

    if not results_list:
        logger.warning("Все тесты завершились без результатов.")
        return

    # 4. Генерация отчета
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"{timestamp}_BATCH_{run_settings['strategy']}_{interval}.xlsx"
    output_path = os.path.join(app_config.PATH_CONFIG["REPORTS_BATCH_TEST_DIR"], report_filename)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    try:
        results_df = pd.DataFrame(results_list)

        # Получаем параметры для заголовка отчета
        # Берем из первого конфига, т.к. они одинаковые
        sample_config = configs[0]

        generator = ExcelReportGenerator(
            results_df=results_df,
            strategy_name=sample_config.strategy_name,
            interval=interval,
            risk_manager_type=sample_config.risk_config["type"],
            strategy_params=sample_config.strategy_params,
            rm_params=sample_config.risk_config
        )
        generator.generate(output_path)
        logger.info(f"Отчет сохранен: {output_path}")

    except Exception as e:
        logger.error(f"Ошибка генерации Excel: {e}", exc_info=True)