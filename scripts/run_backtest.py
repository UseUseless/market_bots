import argparse
import os
from datetime import datetime
import logging

from app.engines.backtest_engine import BacktestEngine
from app.analyzers.analysis_session import AnalysisSession
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS
from app.utils.backtest_logging import setup_backtest_logging
from app.utils.logging_setup import backtest_time_filter  # Фильтр для времени симуляции

from config import BACKTEST_CONFIG, PATH_CONFIG

logger = logging.getLogger('backtester')

def main():
    """
    Главная функция: парсит аргументы, собирает настройки,
    запускает движок бэктеста и затем сессию анализа.
    """
    parser = argparse.ArgumentParser(
        description="Запуск одиночного бэктеста для торговой стратегии."
    )
    parser.add_argument(
        "--strategy", type=str, required=True,
        choices=list(AVAILABLE_STRATEGIES.keys()),
        help="Имя стратегии для тестирования."
    )
    parser.add_argument(
        "--exchange", type=str, required=True,
        choices=['tinkoff', 'bybit'],
        help="Биржа, на данных которой проводится бэктест."
    )
    parser.add_argument(
        "--instrument", type=str, required=True,
        help="Тикер/символ инструмента (например: SBER, BTCUSDT)."
    )
    parser.add_argument(
        "--interval", type=str, required=True,
        help="Таймфрейм для бэктеста."
    )
    parser.add_argument(
        "--rm", dest="risk_manager_type", type=str,
        default="FIXED", choices=list(AVAILABLE_RISK_MANAGERS.keys()),
        help="Модель управления риском."
    )
    args = parser.parse_args()

    # --- 1. Настройка окружения ---
    strategy_class = AVAILABLE_STRATEGIES[args.strategy]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Формируем базовое имя файла для всех артефактов этого запуска
    base_filename = (
        f"{timestamp}_{strategy_class.__name__}_{args.instrument}_"
        f"{args.interval}_RM-{args.risk_manager_type}"
    )

    # Настраиваем логирование
    log_dir = PATH_CONFIG["LOGS_BACKTEST_DIR"]
    log_file_path = os.path.join(log_dir, f"{base_filename}_run.log")
    trade_log_path = os.path.join(log_dir, f"{base_filename}_trades.jsonl")
    setup_backtest_logging(log_file_path)

    logger.info(f"Запуск бэктеста: {base_filename}")
    logger.info(f"Используются параметры по умолчанию из файлов стратегии и риск-менеджера.")

    # --- 2. Сборка единого словаря настроек для движка ---
    backtest_settings = {
        "strategy_class": strategy_class,
        "exchange": args.exchange,
        "instrument": args.instrument,
        "interval": args.interval,
        "risk_manager_type": args.risk_manager_type,
        "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
        "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
        "data_dir": PATH_CONFIG["DATA_DIR"],
        "trade_log_path": trade_log_path,
        "strategy_params": None,  # Движок сам возьмет параметры по умолчанию
        "risk_manager_params": None
    }

    try:
        # --- 3. Запуск движка ---
        engine = BacktestEngine(backtest_settings)
        results = engine.run()

        # --- 4. Анализ результатов через AnalysisSession ---
        if results["status"] == "success" and not results["trades_df"].empty:
            logger.info(f"Бэктест завершен, найдено {len(results['trades_df'])} сделок. Запуск анализатора.")

            analysis_session = AnalysisSession(
                trades_df=results["trades_df"],
                historical_data=results["enriched_data"],
                initial_capital=results["initial_capital"],
                exchange=args.exchange,
                interval=args.interval,
                risk_manager_type=args.risk_manager_type
            )

            # Запускаем генерацию всех отчетов (консольного и графического)
            analysis_session.generate_all_reports(
                base_filename=base_filename,
                report_dir=PATH_CONFIG["REPORTS_BACKTEST_DIR"]
            )
        else:
            logger.warning(f"Бэктест завершен без сделок или с ошибкой: {results.get('message', 'Нет данных')}")

    except Exception:
        # Ловим любые непредвиденные ошибки из фреймворка
        logger.critical("Произошла критическая ошибка во время выполнения бэктеста!", exc_info=True)
    finally:
        # Сбрасываем время симуляции в логгере после завершения
        backtest_time_filter.reset_sim_time()
        logger.info("--- Сессия бэктеста завершена ---")


if __name__ == "__main__":
    main()