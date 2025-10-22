import queue
import argparse
import os
from datetime import datetime
import logging
import pandas as pd

# --- Импорт компонентов новой архитектуры ---
from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent
from core.data_handler import HistoricTinkoffDataHandler, HistoricLocalDataHandler
from core.portfolio import Portfolio
from core.execution import SimulatedExecutionHandler
from analyzer import BacktestAnalyzer

# --- Импорт и регистрация конкретных стратегий ---
from strategies.triple_filter import TripleFilterStrategy
# from strategies.my_awesome_strategy import MyAwesomeStrategy # <-- Пример добавления новой

# --- Реестр доступных стратегий ---
AVAILABLE_STRATEGIES = {
    "triple_filter": TripleFilterStrategy,
    # "my_awesome_strategy": MyAwesomeStrategy, # <-- Пример регистрации новой
}

def setup_logging(log_file_path: str):
    """Настраивает и конфигурирует логгер."""
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    
    file_handler = logging.FileHandler(log_file_path, mode='w')
    file_handler.setFormatter(log_formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

def run_backtest(strategy_class: type, days: int, trade_log_path: str, source: str, figi: str):
    """
    Запускает полный цикл бэктестинга для выбранной стратегии.
    """
    events_queue = queue.Queue()

    # 1. ИНИЦИАЛИЗАЦИЯ КОМПОНЕНТОВ
    logging.info("Инициализация компонентов бэктеста...")
    strategy = strategy_class(events_queue, figi)
    
    if source == 'api':
        data_handler = HistoricTinkoffDataHandler(
            events_queue, figi, days, strategy.candle_interval
        )
    elif source == 'local':
        data_handler = HistoricLocalDataHandler(
            events_queue, figi, strategy.candle_interval
        )
    else:
        raise ValueError(f"Неизвестный источник данных: {source}")
    
    portfolio = Portfolio(events_queue, trade_log_path, strategy)
    execution_handler = SimulatedExecutionHandler(events_queue)
    logging.info(f"Инициализация завершена. Стратегия: '{strategy.name}', FIGI: {figi}, Интервал: {strategy.candle_interval}")

    # 2. ЭТАП ПОДГОТОВКИ ДАННЫХ
    logging.info("Начало этапа подготовки данных...")
    raw_data = data_handler.load_raw_data()
    if raw_data.empty:
        logging.error("Не удалось получить данные для бэктеста. Завершение работы.")
        return
    enriched_data = strategy.prepare_data(raw_data)
    logging.info("Этап подготовки данных завершен.")

    # --- 3. ИСПРАВЛЕННЫЙ ГЛАВНЫЙ ЦИКЛ СОБЫТИЙ ---
    logging.info("Запуск основного цикла обработки событий...")
    
    # Создаем генератор, который будет выдавать нам свечи по одной
    data_generator = (MarketEvent(timestamp=row['time'], figi=figi, data=row) for i, row in enriched_data.iterrows())

    # Начинаем с первой свечи
    try:
        first_event = next(data_generator)
        events_queue.put(first_event)
    except StopIteration:
        logging.warning("Нет данных для запуска бэктеста после подготовки.")
        return

    while True:
        try:
            event = events_queue.get(block=False)
        except queue.Empty:
            # Очередь пуста, значит все последствия предыдущей свечи обработаны.
            # Пора загружать СЛЕДУЮЩУЮ свечу из истории.
            try:
                market_event = next(data_generator)
                events_queue.put(market_event)
                continue # Возвращаемся в начало цикла, чтобы обработать эту новую свечу
            except StopIteration:
                # История кончилась, генератор пуст. Завершаем бэктест.
                break
        else:
            # Маршрутизация событий
            if isinstance(event, MarketEvent):
                portfolio.update_market_price(event)
                strategy.calculate_signals(event)
            elif isinstance(event, SignalEvent):
                portfolio.on_signal(event)
            elif isinstance(event, OrderEvent):
                execution_handler.execute_order(event)
            elif isinstance(event, FillEvent):
                portfolio.on_fill(event)

    logging.info("Основной цикл завершен.")

    # 4. АНАЛИЗ РЕЗУЛЬТАТОВ И ГЕНЕРАЦИЯ ОТЧЕТА
    if not portfolio.closed_trades:
        print("\nБэктест завершен. Сделок не было совершено.")
        # Проверим, не осталась ли открытая позиция в конце
        if portfolio.current_positions:
            print("ВНИМАНИЕ: Бэктест завершился с открытой позицией:")
            print(portfolio.current_positions)
        return
        
    trades_df = pd.DataFrame(portfolio.closed_trades)
    report_filename = os.path.basename(trade_log_path).replace('_trades.csv', '')
    
    try:
        analyzer = BacktestAnalyzer(trades_df, portfolio.initial_capital)
        analyzer.generate_report(report_filename)
    except Exception as e:
        logging.error(f"Ошибка при создании отчета: {e}")

def main():
    parser = argparse.ArgumentParser(description="Фреймворк для запуска торговых ботов.")
    
    parser.add_argument("--mode", type=str, required=True, choices=['backtest', 'sandbox', 'real'], help="Режим работы.")
    parser.add_argument("--strategy", type=str, required=True, help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument("--figi", type=str, help="FIGI инструмента для тестирования (обязателен для backtest).")
    parser.add_argument("--source", type=str, default='api', choices=['api', 'local'], help="Источник данных: 'api' или 'local'.")
    parser.add_argument("--days", type=int, default=90, help="Количество дней для бэктестинга (только для --source api).")
    
    args = parser.parse_args()

    if args.strategy not in AVAILABLE_STRATEGIES:
        print(f"Ошибка: Стратегия '{args.strategy}' не найдена.")
        return

    if args.mode == 'backtest' and not args.figi:
        print("Ошибка: для режима 'backtest' необходимо указать --figi.")
        return

    # Генерация уникальных имен файлов для логов
    LOGS_DIR = "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{timestamp}_{args.strategy}_{args.figi}_{args.mode}"

    log_file_path = os.path.join(LOGS_DIR, f"{base_filename}_run.log")
    trade_log_path = os.path.join(LOGS_DIR, f"{base_filename}_trades.csv")

    setup_logging(log_file_path)
    strategy_class = AVAILABLE_STRATEGIES[args.strategy]

    if args.mode == 'backtest':
        run_backtest(
            strategy_class=strategy_class,
            days=args.days,
            trade_log_path=trade_log_path,
            source=args.source,
            figi=args.figi
        )
    else:
        logging.warning(f"Режим '{args.mode}' еще не реализован.")

if __name__ == "__main__":
    main()