import os
from datetime import datetime
import logging
from typing import Dict, Any, Optional
import queue

from app.engines.backtest_engine import BacktestEngine
from app.analyzers.analysis_session import AnalysisSession
from app.strategies import AVAILABLE_STRATEGIES
from app.utils.backtest_logging import setup_backtest_logging
from app.utils.logging_setup import backtest_time_filter
from config import BACKTEST_CONFIG, PATH_CONFIG, EXCHANGE_SPECIFIC_CONFIG
from app.analyzers.metrics.portfolio_metrics import PortfolioMetricsCalculator
from app.analyzers.metrics.benchmark_metrics import BenchmarkMetricsCalculator

logger = logging.getLogger(__name__)


def _run_and_analyze_single_instrument(settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    "Рабочая единица": Запускает BacktestEngine для одного инструмента и рассчитывает
    полный набор метрик, возвращая их в виде словаря.

    :param settings: Словарь с полной конфигурацией для одного запуска.
    :return: Словарь с рассчитанными метриками или None в случае ошибки/отсутствия сделок.
    """
    try:
        events_queue = queue.Queue()
        engine = BacktestEngine(settings, events_queue)
        results = engine.run()

        if results["status"] == "success" and not results["trades_df"].empty:
            exchange = settings["exchange"]
            annual_factor = EXCHANGE_SPECIFIC_CONFIG[exchange]["SHARPE_ANNUALIZATION_FACTOR"]

            # 1. Метрики по сделкам стратегии
            portfolio_calc = PortfolioMetricsCalculator(
                trades_df=results["trades_df"],
                initial_capital=results["initial_capital"],
                annualization_factor=annual_factor
            )
            portfolio_metrics = portfolio_calc.calculate_all()

            # 2. Метрики для бенчмарка (Buy & Hold)
            bench_calc = BenchmarkMetricsCalculator(
                historical_data=results["enriched_data"],
                initial_capital=results["initial_capital"],
                annualization_factor=annual_factor
            )
            bench_metrics = bench_calc.calculate_all()

            # 3. Собираем все в один словарь для возврата
            full_metrics = {
                **portfolio_metrics,
                'pnl_bh_pct': bench_metrics.get('pnl_pct', 0.0),
                # Добавляем данные, необходимые для генерации отчетов
                "trades_df": results["trades_df"],
                "enriched_data": results["enriched_data"],
                "initial_capital": results["initial_capital"]
            }
            return full_metrics

    except Exception as e:
        instrument = settings.get("instrument", "N/A")
        logger.error(f"Ошибка при обработке инструмента '{instrument}': {e}", exc_info=True)

    return None


def run_single_backtest_flow(settings: Dict[str, Any]):
    """
    Оркестратор для запуска ОДИНОЧНОГО бэктеста с генерацией визуальных отчетов.
    Использует _run_and_analyze_single_instrument для выполнения основной работы.
    """
    strategy_name = settings["strategy"]
    instrument = settings["instrument"]
    interval = settings["interval"]
    risk_manager_type = settings["risk_manager_type"]
    exchange = settings["exchange"]

    strategy_class = AVAILABLE_STRATEGIES[strategy_name]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    base_filename = (
        f"{timestamp}_{strategy_class.__name__}_{instrument}_"
        f"{interval}_RM-{risk_manager_type}"
    )

    log_dir = PATH_CONFIG["LOGS_BACKTEST_DIR"]
    log_file_path = os.path.join(log_dir, f"{base_filename}_run.log")
    trade_log_path = os.path.join(log_dir, f"{base_filename}_trades.jsonl")
    setup_backtest_logging(log_file_path)

    logger.info(f"Запуск потока одиночного бэктеста: {base_filename}")

    # Собираем настройки для движка
    engine_settings = {
        "strategy_class": strategy_class,
        "exchange": exchange,
        "instrument": instrument,
        "interval": interval,
        "risk_manager_type": risk_manager_type,
        "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
        "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
        "data_dir": PATH_CONFIG["DATA_DIR"],
        "trade_log_path": trade_log_path,
        "strategy_params": None,
        "risk_manager_params": None
    }

    try:
        # --- Вызываем нашу новую "рабочую единицу" ---
        analysis_results = _run_and_analyze_single_instrument(engine_settings)

        if analysis_results:
            logger.info(f"Бэктест завершен, найдено {analysis_results['total_trades']} сделок. Генерация отчетов.")

            # Используем AnalysisSession для генерации ВИЗУАЛЬНЫХ отчетов
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
                report_dir=PATH_CONFIG["REPORTS_BACKTEST_DIR"]
            )
        else:
            logger.warning("Бэктест завершен без сделок или с ошибкой. Отчеты не сгенерированы.")

    except Exception:
        logger.critical("Произошла критическая ошибка во время выполнения потока бэктеста!", exc_info=True)
    finally:
        backtest_time_filter.reset_sim_time()
        logger.info("--- Поток одиночного бэктеста завершен ---")