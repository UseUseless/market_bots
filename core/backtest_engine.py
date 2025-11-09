import queue
import logging
import pandas as pd
from typing import Dict, Any

from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent
from core.data_handler import HistoricLocalDataHandler
from core.portfolio import Portfolio
from core.execution import SimulatedExecutionHandler
from core.feature_engine import FeatureEngine
from strategies.base_strategy import BaseStrategy
from utils.context_logger import backtest_time_filter
from utils.file_io import load_instrument_info

logger = logging.getLogger('backtester')


def _initialize_components(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Инициализирует и возвращает все ключевые компоненты системы на основе конфига."""
    logger.info("Инициализация компонентов бэктеста...")
    events_queue = queue.Queue()

    instrument_info = load_instrument_info(
        exchange=settings["exchange"],
        instrument=settings["instrument"],
        interval=settings["interval"],
        data_dir=settings["data_dir"]
    )

    strategy = settings["strategy_class"](
        events_queue,
        settings["instrument"],
        strategy_config=settings["strategy_config"]
    )

    data_handler = HistoricLocalDataHandler(
        events_queue,
        settings["exchange"],
        settings["instrument"],
        settings["interval"],
        data_path=settings["data_dir"]
    )

    feature_engine = FeatureEngine()
    execution_handler = SimulatedExecutionHandler(events_queue)

    portfolio = Portfolio(
        events_queue=events_queue,
        trade_log_file=settings.get("trade_log_path"),  # Может быть None
        strategy=strategy,
        exchange=settings["exchange"],
        initial_capital=settings["initial_capital"],
        commission_rate=settings["commission_rate"],
        interval=settings["interval"],
        risk_manager_type=settings["risk_manager_type"],
        instrument_info=instrument_info,
        risk_config=settings["risk_config"],
        strategy_config=settings["strategy_config"]
    )

    return {
        "events_queue": events_queue, "strategy": strategy, "data_handler": data_handler,
        "portfolio": portfolio, "execution_handler": execution_handler, "feature_engine": feature_engine
    }

def _prepare_data(
        data_handler: HistoricLocalDataHandler,
        feature_engine: FeatureEngine,
        strategy: BaseStrategy,
        risk_manager_type: str,
        risk_config: Dict[str, Any],
        data_slice: pd.DataFrame = None
) -> pd.DataFrame | None:
    """Подготавливает исторические данные, добавляя индикаторы."""
    logger.info("Начало этапа подготовки данных...")

    raw_data = data_slice if data_slice is not None else data_handler.load_raw_data()
    if raw_data.empty:
        logger.error("Не удалось получить данные для бэктеста. Завершение работы.")
        return None

    all_requirements = strategy.required_indicators.copy()
    logger.info(f"Стратегия '{strategy.name}' требует индикаторы: {all_requirements}")

    if risk_manager_type == "ATR":
        atr_requirement = {"name": "atr", "params": {"period": risk_config["ATR_PERIOD"]}}
        all_requirements.append(atr_requirement)
        logger.info(f"AtrRiskManager требует индикатор: {atr_requirement}")

    prepared_data = feature_engine.add_required_features(raw_data, all_requirements) if all_requirements else raw_data
    enriched_data = strategy.prepare_data(prepared_data)

    enriched_data.dropna(inplace=True)
    enriched_data.reset_index(drop=True, inplace=True)

    if len(enriched_data) < strategy.min_history_needed:
        logger.error(f"Ошибка: Недостаточно данных для запуска стратегии '{strategy.name}'.")
        logger.error(
            f"Требуется как минимум {strategy.min_history_needed} свечей, но после подготовки и очистки доступно только {len(enriched_data)}.")
        return None

    if enriched_data.empty:
        logger.warning("Нет данных для запуска бэктеста после подготовки.")
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
    data_generator = (MarketEvent(timestamp=row['time'], instrument=instrument, data=row) for i, row in
                      enriched_data.iterrows())

    while True:
        try:
            event = events_queue.get(block=False)
        except queue.Empty:
            try:
                market_event = next(data_generator)
                events_queue.put(market_event)
                continue
            except StopIteration:
                break
        else:
            try:
                if isinstance(event, MarketEvent):
                    backtest_time_filter.set_sim_time(event.timestamp)
                    portfolio.update_market_price(event)
                    strategy.calculate_signals(event)
                elif isinstance(event, SignalEvent):
                    portfolio.on_signal(event)
                elif isinstance(event, OrderEvent):
                    execution_handler.execute_order(event)
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
        components["data_handler"],
        components["feature_engine"],
        components["strategy"],
        settings["risk_manager_type"],
        settings["risk_config"],
        data_slice=settings.get("data_slice")  # Для WFO
    )

    if enriched_data is None:
        logger.error("Подготовка данных провалилась. Бэктест прерван.")
        return

    _run_event_loop(
        enriched_data, settings["instrument"], components["events_queue"],
        components["portfolio"], components["strategy"], components["execution_handler"]
    )

    # Собираем результаты
    portfolio = components["portfolio"]
    trades_df = pd.DataFrame(portfolio.closed_trades) if portfolio.closed_trades else pd.DataFrame()

    final_capital = portfolio.current_capital
    total_pnl = final_capital - settings["initial_capital"]

    return {
        "status": "success",
        "trades_df": trades_df,
        "final_capital": final_capital,
        "total_pnl": total_pnl,
        "initial_capital": settings["initial_capital"],
        "enriched_data": enriched_data,
        "open_positions": portfolio.current_positions
    }