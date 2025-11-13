# app/engines/backtest_engine.py

import queue
import logging
import pandas as pd
from typing import Dict, Any

# --- Модели данных ---
from app.core.models.event import MarketEvent, SignalEvent, OrderEvent, FillEvent

# --- Компоненты ядра ---
from app.core.portfolio import Portfolio
from app.core.data.local_handler import HistoricLocalDataHandler
from app.core.execution.simulated import SimulatedExecutionHandler
from app.core.risk.sizer import FixedRiskSizer
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS
from app.core.services.risk_monitor import RiskMonitor
from app.core.services.order_manager import OrderManager
from app.core.services.fill_processor import FillProcessor

# --- Вспомогательные модули ---
from app.strategies.base_strategy import BaseStrategy
from app.utils.logging_setup import backtest_time_filter
from app.utils.file_io import load_instrument_info
from config import PATH_CONFIG, BACKTEST_CONFIG

logger = logging.getLogger('backtester')


def _initialize_components(settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Инициализирует и возвращает все ключевые компоненты системы на основе конфига.
    Эта функция выступает в роли "Сборщика" (Assembler), реализуя принцип
    инверсии зависимостей (Dependency Injection).
    """
    logger.info("Инициализация компонентов бэктеста...")
    events_queue = queue.Queue()

    # --- 1. Загрузка метаданных об инструменте ---
    instrument_info = load_instrument_info(
        exchange=settings["exchange"],
        instrument=settings["instrument"],
        interval=settings["interval"],
        data_dir=settings["data_dir"]
    )

    # --- 2. Инициализация Стратегии ---
    strategy_class = settings["strategy_class"]
    strategy_params = settings.get("strategy_params") or strategy_class.get_default_params()
    strategy = strategy_class(
        events_queue,
        settings["instrument"],
        params=strategy_params,
        risk_manager_type=settings["risk_manager_type"]
    )

    # --- 3. Инициализация Компонентов Ядра (Core) ---
    # 3.1. Риск-менеджер и Сайзер
    rm_class = AVAILABLE_RISK_MANAGERS[settings["risk_manager_type"]]
    rm_params = settings.get("risk_manager_params") or rm_class.get_default_params()
    risk_manager = rm_class(params=rm_params)
    position_sizer = FixedRiskSizer()  # Пока у нас только один сайзер

    # 3.2. Сервисы
    risk_monitor = RiskMonitor(events_queue)
    order_manager = OrderManager(events_queue, risk_manager, position_sizer, instrument_info)
    fill_processor = FillProcessor(
        trade_log_file=settings.get("trade_log_path"),
        strategy=strategy,
        risk_manager=risk_manager,
        exchange=settings["exchange"],
        interval=settings["interval"]
    )

    # 3.3. Исполнитель
    execution_handler = SimulatedExecutionHandler(
        events_queue,
        commission_rate=settings["commission_rate"],
        slippage_config=BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {})
    )

    # 3.4. Поставщик данных
    data_handler = HistoricLocalDataHandler(
        exchange=settings["exchange"],
        instrument_id=settings["instrument"],
        interval_str=settings["interval"],
        data_path=settings["data_dir"]
    )

    # --- 4. Сборка "Легкого" Portfolio ---
    portfolio = Portfolio(
        events_queue=events_queue,
        initial_capital=settings["initial_capital"],
        risk_monitor=risk_monitor,
        order_manager=order_manager,
        fill_processor=fill_processor
    )

    return {
        "events_queue": events_queue,
        "strategy": strategy,
        "data_handler": data_handler,
        "portfolio": portfolio,
        "execution_handler": execution_handler
    }


def _prepare_data(
        strategy: BaseStrategy,
        settings: Dict[str, Any]
) -> pd.DataFrame | None:
    """Подготавливает исторические данные, полностью делегируя это стратегии."""
    logger.info("Начало этапа подготовки данных...")

    data_slice = settings.get("data_slice")

    if data_slice is not None:
        raw_data = data_slice
    else:
        # Если срез данных не передан, загружаем их
        data_handler = HistoricLocalDataHandler(
            exchange=settings["exchange"],
            instrument_id=settings["instrument"],
            interval_str=settings["interval"],
            data_path=settings.get("data_dir", PATH_CONFIG["DATA_DIR"])
        )
        raw_data = data_handler.load_raw_data()

    if raw_data is None or raw_data.empty:
        logger.error(f"Не удалось получить данные для бэктеста по инструменту {settings['instrument']}.")
        return None

    enriched_data = strategy.process_data(raw_data.copy())

    if len(enriched_data) < strategy.min_history_needed:
        logger.error(f"Ошибка: Недостаточно данных для запуска стратегии '{strategy.name}'. "
                     f"Требуется {strategy.min_history_needed}, доступно {len(enriched_data)}.")
        return None

    logger.info("Этап подготовки данных завершен.")
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
    logger.info("Запуск основного цикла обработки событий...")
    data_generator = (row for _, row in enriched_data.iterrows())

    while True:
        try:
            event = events_queue.get(block=False)
        except queue.Empty:
            try:
                current_candle = next(data_generator)
                market_event = MarketEvent(
                    timestamp=current_candle['time'],
                    instrument=instrument,
                    data=current_candle
                )
                events_queue.put(market_event)
                continue
            except StopIteration:
                break  # Данные закончились, выходим из цикла
        else:
            try:
                if isinstance(event, MarketEvent):
                    backtest_time_filter.set_sim_time(event.timestamp)
                    portfolio.update_market_price(event)
                    strategy.on_market_event(event)
                elif isinstance(event, SignalEvent):
                    portfolio.on_signal(event)
                elif isinstance(event, OrderEvent):
                    # Передаем последнюю свечу в симулятор для расчета цены
                    execution_handler.execute_order(event, current_candle)
                elif isinstance(event, FillEvent):
                    portfolio.on_fill(event)
            except Exception as e:
                logger.error(f"Критическая ошибка при обработке события {type(event).__name__}: {e}", exc_info=True)
                break

    backtest_time_filter.reset_sim_time()
    logger.info("Основной цикл завершен.")


def run_backtest_session(settings: Dict[str, Any]) -> Dict[str, Any]:
    """
    Главная функция-движок. Запускает одну сессию бэктеста и возвращает результаты.
    """
    components = _initialize_components(settings)

    enriched_data = _prepare_data(
        strategy=components["strategy"],
        settings=settings
    )

    if enriched_data is None:
        return {"status": "error", "message": "Data preparation failed", "trades_df": pd.DataFrame(),
                "open_positions": {}}

    _run_event_loop(
        enriched_data, settings["instrument"], components["events_queue"],
        components["portfolio"], components["strategy"], components["execution_handler"]
    )

    # Собираем результаты из PortfolioState
    portfolio = components["portfolio"]
    trades_df = pd.DataFrame(portfolio.state.closed_trades) if portfolio.state.closed_trades else pd.DataFrame()

    return {
        "status": "success",
        "trades_df": trades_df,
        "final_capital": portfolio.state.current_capital,
        "total_pnl": portfolio.state.current_capital - settings["initial_capital"],
        "initial_capital": settings["initial_capital"],
        "enriched_data": enriched_data,
        "open_positions": portfolio.state.positions
    }