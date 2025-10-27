import queue    # Создание очереди
import argparse # Аргументы для запуска в командной строке
import os
from datetime import datetime
import logging
import pandas as pd

from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent # События, которые становятся в очередь
from core.data_handler import HistoricLocalDataHandler # Для загрузки данных из файла
from core.portfolio import Portfolio # Менеджер, контролирующий, логику и события
from core.execution import SimulatedExecutionHandler # Исполнитель любых ордеров
from analyzer import BacktestAnalyzer # Создает аналитический отчет и график
from utils.context_logger import backtest_time_filter # Добавляет время свечи в логи

# --- Импорт и регистрация конкретных стратегий ---
from strategies.triple_filter import TripleFilterStrategy
# from strategies.my_awesome_strategy import MyAwesomeStrategy # Пример добавления новой

# --- Реестр доступных стратегий ---
AVAILABLE_STRATEGIES = {
    "triple_filter": TripleFilterStrategy,
    # "my_awesome_strategy": MyAwesomeStrategy, # Пример регистрации новой
}

def setup_logging(log_file_path: str, backtest_mode: bool):
    """Настраивает и конфигурирует логгер."""

    # Выбираем формат вывода логов в зависимости от режима работы
    if backtest_mode:
        # Для бэктеста используем время симуляции (из файла со свечами время свечи)
        log_formatter = logging.Formatter('%(sim_time)s - %(levelname)s - %(message)s')
    else:
        # Используем реальное системное время
        log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Создаем папку для логов
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

    # Запись логов в файл
    file_handler = logging.FileHandler(log_file_path, mode='w')
    file_handler.setFormatter(log_formatter)

    # Вывод логов в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    logger = logging.getLogger()
    # Устанавливаем уровень сообщений (INFO и выше)
    logger.setLevel(logging.INFO)
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Если бэктест, используем фильтр для замены системного времени на время симуляции
    if backtest_mode:
        logger.addFilter(backtest_time_filter)

def run_backtest(strategy_class: type, trade_log_path: str, figi: str):
    """
    Запускает полный цикл бэктестинга для выбранной стратегии.
    Полный цикл, это:
    Логирование всех действий, чтение скачанных исторических данных,
    проход стратегии по каждой свече с соответствующими покупками, продажами (или игнорир),
    сохранением отчета и краткого (пока что) анализа
    """

    # Создаем очередь по которой будут идти все события
    events_queue = queue.Queue()

    # 1. ИНИЦИАЛИЗАЦИЯ КОМПОНЕНТОВ
    # Создаем экземпляры:
    logging.info("Инициализация компонентов бэктеста...")
    # Стратегия
    strategy = strategy_class(events_queue, figi)
    # Обработка данных
    data_handler = HistoricLocalDataHandler(
        events_queue, figi, strategy.candle_interval
    )
    # Портфель - риск-менеджер (разные расчеты по портфелю и ордерам)
    portfolio = Portfolio(events_queue, trade_log_path, strategy)
    # Брокер - исполнитель ордеров
    execution_handler = SimulatedExecutionHandler(events_queue)
    logging.info(f"Инициализация завершена. Стратегия: '{strategy.name}', FIGI: {figi}, Интервал: {strategy.candle_interval}")

    # 2. ЭТАП ПОДГОТОВКИ ДАННЫХ
    logging.info("Начало этапа подготовки данных...")
    # Загружаем данные (TOHLCV (time, open, high, low, close, volume)) из локального файла
    raw_data = data_handler.load_raw_data()
    # Если файла не нашлось, возвращает пустой df
    if raw_data.empty:
        logging.error("Не удалось получить данные для бэктеста. Завершение работы.")
        return

    # Обработка данных стратегией (создание новых индикаторов, фичей)
    enriched_data = strategy.prepare_data(raw_data)

    if enriched_data.empty:
        logging.warning("Нет данных для запуска бэктеста после подготовки (возможно, из-за короткого периода истории).")
        return

    start_date = enriched_data['time'].iloc[0]
    end_date = enriched_data['time'].iloc[-1]
    logging.info("Этап подготовки данных завершен.")

    # --- 3. ГЛАВНЫЙ ЦИКЛ СОБЫТИЙ ---
    logging.info("Запуск основного цикла обработки событий...")
    
    # Создаем генератор, который будет выдавать нам свечи (строки pd.df, то есть и другие данные в строке) по одной
    data_generator = (MarketEvent(timestamp=row['time'], figi=figi, data=row) for i, row in enriched_data.iterrows())

    # Кладем в очередь самое первое событие - первую свечу
    first_event = next(data_generator)
    events_queue.put(first_event)

    # Бесконечный цикл, который будет работать, пока не закончатся данные
    while True:
        try:
            # Пытаемся не блокируя работу программы достать следующее событие
            event = events_queue.get(block=False)
        # Очередь пуста, значит все последствия предыдущей свечи обработаны.
        except queue.Empty:
            # Загружаем следующую строку данных
            try:
                market_event = next(data_generator)
                events_queue.put(market_event)
                # Возвращаемся в начало цикла, чтобы обработать эту новую свечу
                continue
            except StopIteration:
                # История кончилась, генератор пуст. Завершаем бэктест (блок while)
                break
        # Выполняется, если мы достали событие
        else:
            # MarketEvent это первоначальное событие
            # Поэтому обновляем время в логгере
            if isinstance(event, MarketEvent):
                backtest_time_filter.set_sim_time(event.timestamp)
            # --- МАРШРУТИЗАТОР СОБЫТИЙ ---
            # Проверяем класс события и отправляем соответствующему обработчику
            if isinstance(event, MarketEvent):
                # Рыночные данные отправляем в Портфель для сохранения данных свечи и проверки есть ли уже
                # по данному инструменту позиции (лоты) (может их стоит закрыть по SL/TP)
                portfolio.update_market_price(event)
                # В Стратегию для расчета сигнала
                strategy.calculate_signals(event)
            elif isinstance(event, SignalEvent):
                # Сигнал от стратегии отправляем в Портфель на решение об открытии/закрытии позиции
                portfolio.on_signal(event)
            elif isinstance(event, OrderEvent):
                # Выполняем ордер
                execution_handler.execute_order(event)
            elif isinstance(event, FillEvent):
                # Расчеты статистики и сохранение после закрытия позиции
                portfolio.on_fill(event)

    # Сбрасываем время симуляции в логгере после окончания цикла
    backtest_time_filter.reset_sim_time()
    logging.info("Основной цикл завершен.")

    # 4. АНАЛИЗ РЕЗУЛЬТАТОВ И ГЕНЕРАЦИЯ ОТЧЕТА
    # Если сделки были, генерируем отчет.
    if portfolio.closed_trades:
        time_period_days = (end_date - start_date).days
        logging.info(
            f"Обнаружено {len(portfolio.closed_trades)} закрытых сделок "
            f"за период ~{time_period_days} дней (с {start_date.date()} по {end_date.date()}). "
            f"Запуск анализатора..."
        )
        trades_df = pd.DataFrame(portfolio.closed_trades)
        report_filename = os.path.basename(trade_log_path).replace('_trades.csv', '')
        try:
            analyzer = BacktestAnalyzer(trades_df, portfolio.initial_capital)
            analyzer.generate_report(report_filename)
        except Exception as e:
            logging.error(f"Ошибка при создании отчета: {e}")
    # Если сделок не было, выводим сообщение.
    else:
        logging.info("Бэктест завершен. Закрытых сделок не было совершено.")

    # Проверка на открытые позиции
    if portfolio.current_positions:
        # Используем logging.warning, чтобы это сообщение было хорошо заметно.
        logging.warning("ВНИМАНИЕ: Бэктест завершился с открытой позицией:")
        # Логируем детали открытой позиции.
        for figi, pos_data in portfolio.current_positions.items():
            logging.warning(f" - {figi}: {pos_data}")

def main():
    # Создаем парсер аргументов командной строки и сами аргументы для запуска программы
    parser = argparse.ArgumentParser(description="Фреймворк для запуска торговых ботов.")
    
    parser.add_argument("--mode", type=str, required=True, choices=['backtest', 'sandbox', 'real'], help="Режим работы.")
    parser.add_argument("--strategy", type=str, required=True, help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument("--figi", type=str, required=True, help="FIGI инструмента для тестирования (обязателен для backtest).")

    args = parser.parse_args()

    # Проверка на существование стратегии
    if args.strategy not in AVAILABLE_STRATEGIES:
        print(f"Ошибка: Стратегия '{args.strategy}' не найдена.")
        return

    strategy_class = AVAILABLE_STRATEGIES[args.strategy]

    # Генерация уникальных имен файлов для логов
    LOGS_DIR = "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{timestamp}_{args.strategy}_{args.figi}_{args.mode}"

    # Создаем полные пути для файла с логами выполнения и файла с логами сделок
    log_file_path = os.path.join(LOGS_DIR, f"{base_filename}_run.log")
    trade_log_path = os.path.join(LOGS_DIR, f"{base_filename}_trades.csv")

    is_backtest = args.mode == 'backtest'

    # Запускаем настройку логгера
    setup_logging(log_file_path, backtest_mode=is_backtest)


    # В зависимости от выбранного режима, запускаем соответствующую функцию (бэктест или лайв, к примеру)
    if is_backtest:
        # Запускам бэктест
        run_backtest(
            strategy_class=strategy_class,
            trade_log_path=trade_log_path,
            figi=args.figi
        )
    else:
        # ToDo: Реализовать sandbox
        logging.warning(f"Режим '{args.mode}' еще не реализован.")

if __name__ == "__main__":
    main()