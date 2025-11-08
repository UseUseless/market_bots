import argparse # Аргументы для запуска в командной строке
import os
from datetime import datetime
import logging
from typing import get_args, Dict, Any

from core.backtest_engine import run_backtest_session
from analyzer import BacktestAnalyzer # Создает аналитический отчет и график
from utils.context_logger import backtest_time_filter # Добавляет время свечи в логи
from core.risk_manager import RiskManagerType

from config import BACKTEST_CONFIG, PATH_CONFIG, RISK_CONFIG, STRATEGY_CONFIG

from strategies import AVAILABLE_STRATEGIES

logger = logging.getLogger('backtester')

def setup_logging(log_file_path: str) -> None:
    """Настраивает и конфигурирует логгер."""
    # Используем глобальный модуль logging для создания форматтера
    log_formatter = logging.Formatter('%(sim_time)s - %(levelname)s - %(message)s')

    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

    file_handler = logging.FileHandler(log_file_path, mode='w')
    file_handler.setFormatter(log_formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    app_logger = logging.getLogger('backtester')
    app_logger.setLevel(logging.INFO) # Возвращаем на INFO

    if app_logger.hasHandlers():
        app_logger.handlers.clear()

    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    app_logger.addFilter(backtest_time_filter)
    app_logger.propagate = False

    # "Заглушаем" шумные библиотеки, чтобы они не мешали
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)


def process_and_analyze_results(backtest_results: Dict[str, Any], settings: Dict[str, Any]):
    """Обрабатывает и анализирует результаты, полученные от движка бэктеста."""
    if backtest_results["status"] != "success":
        logger.error(f"Бэктест завершился с ошибкой: {backtest_results.get('message')}")
        return

    trades_df = backtest_results["trades_df"]
    enriched_data = backtest_results["enriched_data"]

    if not trades_df.empty:
        start_date = enriched_data['time'].iloc[0]
        end_date = enriched_data['time'].iloc[-1]
        time_period_days = (end_date - start_date).days
        logger.info(f"Бэктест охватил период ~{time_period_days} дней (с {start_date.date()} по {end_date.date()}).")

        logger.info(f"Обнаружено {len(trades_df)} закрытых сделок. Запуск анализатора...")
        report_filename = os.path.basename(settings["trade_log_path"]).replace('_trades.jsonl', '')
        analyzer = BacktestAnalyzer(
            trades_df=trades_df,
            historical_data=enriched_data,
            initial_capital=backtest_results["initial_capital"],
            interval=settings["interval"],
            risk_manager_type=settings["risk_manager_type"],
            exchange=settings["exchange"]
        )
        analyzer.generate_report(report_filename)
    else:
        logger.info("Бэктест завершен. Закрытых сделок не было совершено.")

    open_positions = backtest_results["open_positions"]
    if open_positions:
        logger.warning("ВНИМАНИЕ: Бэктест завершился с открытой позицией:")
        for instrument, pos_data in open_positions.items():
            logger.warning(f" - {instrument}: {pos_data}")
    else:
        logger.info("Открытые позиции на конец бэктеста отсутствуют.")


def main():
    """
    Главная "точка входа" в программу.
    Отвечает за парсинг аргументов командной строки и подготовку параметров для запуска.
    """
    # Создаем парсер аргументов командной строки и сами аргументы для запуска программы
    parser = argparse.ArgumentParser(description="Фреймворк для запуска торговых ботов.")

    # Возможные варианты для командной строки
    valid_rms = get_args(RiskManagerType)

    parser.add_argument(
        "--strategy",
        type=str,
        required=True,
        help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument(
        "--exchange",
        type=str,
        required=True,
        choices=['tinkoff', 'bybit'],
        help="Биржа, на данных которой проводится бэктест.")
    parser.add_argument(
        "--instrument",
        type=str,
        required=True,
        help="Тикер/символ инструмента для тестирования (например: SBER, BTCUSDT).")
    parser.add_argument(
        "--rm", # Добавляем короткое имя --rm
        "--risk_manager",
        dest="risk_manager_type",
        type=str,
        default="FIXED",
        choices=valid_rms,
        help="Модель управления риском (расчета SL/TP)."
    )
    parser.add_argument(
        "--interval",
        type=str,
        default=None,
        help="Переопределяет таймфрейм для бэктеста. Если не указан, используется рекомендуемый из стратегии."
    )

    args = parser.parse_args()

    # Проверка на существование стратегии
    if args.strategy not in AVAILABLE_STRATEGIES:
        print(f"Ошибка: Стратегия '{args.strategy}' не найдена.")
        return

    # Берем на будущее на каком интервале используется стратегия (для логов и поиска файлов)
    # Из стратегии или из командной строки
    strategy_class = AVAILABLE_STRATEGIES[args.strategy]
    current_interval = args.interval or strategy_class.candle_interval

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Имена логов для текущего запуска бэктеста
    base_filename = f"{timestamp}_{strategy_class.__name__}_{args.instrument}_{current_interval}_RM-{args.risk_manager_type}_backtest"

    # Генерация уникальных имен файлов для логов
    LOGS_DIR = PATH_CONFIG["LOGS_DIR"]
    # Создаем полные пути для файла с логами выполнения и файла с логами сделок
    log_file_path = os.path.join(LOGS_DIR, f"{base_filename}_run.log")
    trade_log_path = os.path.join(LOGS_DIR, f"{base_filename}_trades.jsonl")

    # Запускаем настройку логгера
    setup_logging(log_file_path)

    logger.info(
        f"Запуск бэктеста: Стратегия='{strategy_class.__name__}', Инструмент='{args.instrument}', Интервал='{current_interval}'")
    risk_params_info = (
        f"Risk % (L/S): {RISK_CONFIG['DEFAULT_RISK_PERCENT_LONG']}%/"
        f"{RISK_CONFIG['DEFAULT_RISK_PERCENT_SHORT']}%"
    )
    if args.risk_manager_type == "ATR":
        risk_params_info += (
            f", ATR Period: {RISK_CONFIG['ATR_PERIOD']}, "
            f"SL/TP Multipliers: {RISK_CONFIG['ATR_MULTIPLIER_SL']}/"
            f"{RISK_CONFIG['ATR_MULTIPLIER_TP']}"
        )
    logger.info(f"Параметры риска ({args.risk_manager_type}): {risk_params_info}")

    backtest_settings = {
        "strategy_class": strategy_class,
        "exchange": args.exchange,
        "instrument": args.instrument,
        "interval": current_interval,
        "risk_manager_type": args.risk_manager_type,
        "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
        "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
        "data_dir": PATH_CONFIG["DATA_DIR"],
        "trade_log_path": trade_log_path,
        "strategy_config": STRATEGY_CONFIG,
        "risk_config": RISK_CONFIG,
    }

    try:
        backtest_results = run_backtest_session(backtest_settings)
        process_and_analyze_results(backtest_results, backtest_settings)

    except Exception as e:
        logger.critical("Неперехваченное исключение на верхнем уровне!", exc_info=True)

if __name__ == "__main__":
    main()