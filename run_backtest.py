import queue    # Создание очереди
import argparse # Аргументы для запуска в командной строке
import os
from datetime import datetime
import logging
import pandas as pd
from typing import get_args, Type, Dict, Optional, Any

from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent, Event # События, которые становятся в очередь
from core.data_handler import HistoricLocalDataHandler # Для загрузки данных из файла
from core.portfolio import Portfolio # Менеджер, контролирующий, логику и события
from core.execution import SimulatedExecutionHandler # Исполнитель любых ордеров
from core.feature_engine import FeatureEngine # Расчет "общих" индикаторов
from core.risk_manager import RiskManagerType # Модель риск-менеджера - как считается SL/TP/quantity

from analyzer import BacktestAnalyzer # Создает аналитический отчет и график
from utils.context_logger import backtest_time_filter # Добавляет время свечи в логи
from utils.file_io import load_instrument_info

from config import BACKTEST_CONFIG, PATH_CONFIG, RISK_CONFIG
from strategies.base_strategy import BaseStrategy

from strategies import AVAILABLE_STRATEGIES

def _initialize_components(
        strategy_class: Type[BaseStrategy],
        exchange: str,
        instrument: str,
        interval: str,
        risk_manager_type: str,
        trade_log_path: str,
        initial_capital: float,
        commission_rate: float
) -> Dict[str, Any]:
    """Инициализирует и возвращает все ключевые компоненты системы."""
    logging.info("Инициализация компонентов бэктеста...")
    # Создаем экземпляры:
    # Создаем очередь по которой будут идти все события
    events_queue = queue.Queue()
    # Загружаем метаданные об инструменте
    data_dir = PATH_CONFIG["DATA_DIR"]
    instrument_info = load_instrument_info(exchange=exchange, instrument=instrument, interval=interval, data_dir=data_dir)
    # Стратегия
    strategy = strategy_class(events_queue, instrument)
    # Обработка данных
    data_handler = HistoricLocalDataHandler(events_queue, exchange, instrument, interval, data_path=data_dir)
    # Для расчета ATR (на сколько входить в позицию относительно волатильности рынка)
    # В целом добавляет обшие расчетные данные в pd.DF
    feature_engine = FeatureEngine()
    # Брокер - исполнитель ордеров
    execution_handler = SimulatedExecutionHandler(events_queue)
    # Портфель - риск-менеджер (разные расчеты по портфелю и ордерам)
    portfolio = Portfolio(events_queue=events_queue,
                          trade_log_file=trade_log_path,
                          strategy=strategy,
                          exchange=exchange,
                          initial_capital=initial_capital,
                          commission_rate=commission_rate,
                          interval=interval,
                          risk_manager_type=risk_manager_type,
                          instrument_info=instrument_info
                          )

    # Информация о SL/TP для логов
    risk_params_info = (
        f"Risk % (L/S): {RISK_CONFIG['DEFAULT_RISK_PERCENT_LONG']}%/"
        f"{RISK_CONFIG['DEFAULT_RISK_PERCENT_SHORT']}%"
    )
    if risk_manager_type == "ATR":
        risk_params_info += (
            f", ATR Period: {RISK_CONFIG['ATR_PERIOD']}, "
            f"SL/TP Multipliers: {RISK_CONFIG['ATR_MULTIPLIER_SL']}/"
            f"{RISK_CONFIG['ATR_MULTIPLIER_TP']}"
        )

    logging.info(f"Инициализация завершена. Стратегия: '{strategy.name}', Инструмент: {instrument}, Интервал: {risk_manager_type}")
    logging.info(f"Параметры риска ({risk_manager_type}): {risk_params_info}")

    return {
        "events_queue": events_queue, "strategy": strategy, "data_handler": data_handler,
        "portfolio": portfolio, "execution_handler": execution_handler, "feature_engine": feature_engine
    }


def _prepare_data(
        data_handler: HistoricLocalDataHandler,
        feature_engine: FeatureEngine,
        strategy: BaseStrategy
) -> Optional[pd.DataFrame]:
    """Загружает и подготавливает исторические данные."""
    logging.info("Начало этапа подготовки данных...")
    # Загружаем данные (TOHLCV (time, open, high, low, close, volume)) из локального файла
    raw_data = data_handler.load_raw_data()
    # Если файла не нашлось, возвращает пустой df
    if raw_data.empty:
        logging.error("Не удалось получить данные для бэктеста. Завершение работы.")
        return

    # Этап 1: Добавляем общие фичи (например, ATR)
    common_features_data = feature_engine.add_common_features(raw_data)
    # Этап 2: Стратегия добавляет свои специфичные фичи
    enriched_data = strategy.prepare_data(common_features_data)

    if enriched_data.empty:
        logging.warning("Нет данных для запуска бэктеста после подготовки (возможно, из-за короткого периода истории).")
        return

    logging.info("Этап подготовки данных завершен.")
    return enriched_data


def _run_event_loop(
        enriched_data: pd.DataFrame,
        instrument: str,
        events_queue: queue.Queue,
        portfolio: Portfolio,
        strategy: BaseStrategy,
        execution_handler: SimulatedExecutionHandler
) -> None:
    """Запускает главный цикл обработки событий."""
    logging.info("Запуск основного цикла обработки событий...")

    # Создаем генератор, который будет выдавать нам свечи (строки pd.df, то есть и другие данные в строке) по одной
    data_generator = (MarketEvent(timestamp=row['time'], instrument=instrument, data=row) for i, row in enriched_data.iterrows())

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


def _analyze_results(
    portfolio: Portfolio,
    enriched_data: pd.DataFrame,
    trade_log_path: str,
    initial_capital: float,
    interval: str,
    risk_manager_type: str
) -> None:
    """Анализирует результаты бэктеста и генерирует отчеты."""
    if portfolio.closed_trades:
        # Время для логирования
        start_date = enriched_data['time'].iloc[0]
        end_date = enriched_data['time'].iloc[-1]
        time_period_days = (end_date - start_date).days
        logging.info(
            f"Бэктест завершен. Обнаружено {len(portfolio.closed_trades)} закрытых сделок "
            f"за период ~{time_period_days} дней (с {start_date.date()} по {end_date.date()}). "
            f"Запуск анализатора..."
        )
        trades_df = pd.DataFrame(portfolio.closed_trades)
        report_filename = os.path.basename(trade_log_path).replace('_trades.jsonl', '')
        try:
            analyzer = BacktestAnalyzer(
                trades_df=trades_df,
                historical_data=enriched_data,
                initial_capital=initial_capital,
                interval=interval,
                risk_manager_type=risk_manager_type
            )
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
        for instrument, pos_data in portfolio.current_positions.items():
            logging.warning(f" - {instrument}: {pos_data}")
    else:
        logging.info("Открытые позиции отсутствуют.")

def setup_logging(log_file_path: str) -> None:
    """Настраивает и конфигурирует логгер."""

    # Формат вывода логов для бэктеста с использованием времени симуляции
    log_formatter = logging.Formatter('%(sim_time)s - %(levelname)s - %(message)s')

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

    # Используем фильтр для замены системного времени на время симуляции
    logger.addFilter(backtest_time_filter)

def run_backtest(trade_log_path: str,
                 exchange: str,
                 interval: str,
                 risk_manager_type: str,
                 strategy_class: Type[BaseStrategy],
                 instrument: str) -> None:
    """
    Запускает полный цикл бэктестинга для выбранной стратегии.
    Полный цикл, это:
    Логирование всех действий, чтение скачанных исторических данных,
    проход стратегии по каждой свече с соответствующими покупками, продажами (или игнорир),
    сохранением отчета и краткого (пока что) анализа
    """

    initial_capital = BACKTEST_CONFIG["INITIAL_CAPITAL"]
    commission_rate = BACKTEST_CONFIG["COMMISSION_RATE"]

    # Создаём экземпляры необходимых компонентов
    components = _initialize_components(
        strategy_class, exchange, instrument, interval, risk_manager_type,
        trade_log_path, initial_capital, commission_rate
    )

    # Подготавливаем данные
    enriched_data = _prepare_data(
        components["data_handler"], components["feature_engine"], components["strategy"]
    )

    # Не пускает None дальше.
    if enriched_data is None:
        logging.error("Подготовка данных провалилась. Бэктест прерван.")
        return  # Выходим из run_backtest

    # Запускаем основной цикл
    _run_event_loop(
        enriched_data, instrument, components["events_queue"],
        components["portfolio"], components["strategy"], components["execution_handler"]
    )

    # Выполняем анализ результатов
    # Если сделки были, генерируем отчет.
    _analyze_results(
        components["portfolio"], enriched_data, trade_log_path,
        initial_capital, interval, risk_manager_type
    )

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
    current_interval = args.interval or AVAILABLE_STRATEGIES[args.strategy].candle_interval
    risk_manager_type = args.risk_manager_type
    strategy_class = AVAILABLE_STRATEGIES[args.strategy]
    instrument = args.instrument


    # Генерация уникальных имен файлов для логов
    LOGS_DIR = PATH_CONFIG["LOGS_DIR"]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Имена логов для текущего запуска бэктеста
    base_filename = f"{timestamp}_{strategy_class.__name__}_{args.instrument}_{current_interval}_RM-{args.risk_manager_type}_backtest"

    # Создаем полные пути для файла с логами выполнения и файла с логами сделок
    log_file_path = os.path.join(LOGS_DIR, f"{base_filename}_run.log")
    trade_log_path = os.path.join(LOGS_DIR, f"{base_filename}_trades.jsonl")

    # Запускаем настройку логгера
    setup_logging(log_file_path)

    # В зависимости от выбранного режима, запускаем соответствующую функцию (бэктест или лайв, к примеру)
    run_backtest(
        trade_log_path=trade_log_path,
        exchange=args.exchange,
        interval=current_interval,
        risk_manager_type=args.risk_manager_type,
        strategy_class=strategy_class,
        instrument=args.instrument,
    )

if __name__ == "__main__":
    main()