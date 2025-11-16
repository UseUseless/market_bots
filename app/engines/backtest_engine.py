import queue
import logging
import pandas as pd
from typing import Dict, Any

from app.core.models.event import MarketEvent, SignalEvent, OrderEvent, FillEvent

from app.core.portfolio import Portfolio
from app.core.data.local_handler import HistoricLocalDataHandler
from app.core.execution.simulated import SimulatedExecutionHandler
from app.core.risk.sizer import FixedRiskSizer
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS
from app.core.services.risk_monitor import RiskMonitor
from app.core.services.order_manager import OrderManager
from app.core.services.fill_processor import FillProcessor

from app.strategies.base_strategy import BaseStrategy
from app.utils.logging_setup import backtest_time_filter
from app.utils.file_io import load_instrument_info
from config import PATH_CONFIG, BACKTEST_CONFIG

logger = logging.getLogger('backtester')

class BacktestEngine:
    """
    Оркестратор для запуска одной сессии бэктеста.
    Инкапсулирует всю логику инициализации компонентов, подготовки данных
    и запуска основного цикла обработки событий.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Инициализирует движок с заданной конфигурацией.

        :param settings: Словарь с полной конфигурацией для одного бэктеста.
        """
        self.settings = settings
        self.components: Dict[str, Any] = {}

    def _initialize_components(self) -> None:
        """
        Приватный метод для инициализации и сборки всех компонентов системы.
        Реализует принцип Dependency Injection.
        """
        logger.info("Инициализация компонентов бэктеста...")
        events_queue = queue.Queue()
        self.components['events_queue'] = events_queue

        instrument_info = load_instrument_info(
            exchange=self.settings["exchange"],
            instrument=self.settings["instrument"],
            interval=self.settings["interval"],
            data_dir=self.settings.get("data_dir", PATH_CONFIG["DATA_DIR"])
        )

        strategy_class = self.settings["strategy_class"]
        strategy_params = self.settings.get("strategy_params") or strategy_class.get_default_params()
        strategy = strategy_class(
            events_queue,
            self.settings["instrument"],
            params=strategy_params,
            risk_manager_type=self.settings["risk_manager_type"]
        )
        self.components['strategy'] = strategy

        rm_class = AVAILABLE_RISK_MANAGERS[self.settings["risk_manager_type"]]
        rm_params = self.settings.get("risk_manager_params") or rm_class.get_default_params()
        risk_manager = rm_class(params=rm_params)
        position_sizer = FixedRiskSizer()

        risk_monitor = RiskMonitor(events_queue)
        order_manager = OrderManager(events_queue, risk_manager, position_sizer, instrument_info)

        fill_processor = FillProcessor(
            trade_log_file=self.settings.get("trade_log_path"),
            exchange=self.settings["exchange"],
            interval=self.settings["interval"],
            strategy_name=strategy.name,
            risk_manager_name=risk_manager.__class__.__name__,
            risk_manager_params=rm_params
        )

        execution_handler = SimulatedExecutionHandler(
            events_queue,
            commission_rate=self.settings["commission_rate"],
            slippage_config=BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {})
        )
        self.components['execution_handler'] = execution_handler

        portfolio = Portfolio(
            events_queue=events_queue,
            initial_capital=self.settings["initial_capital"],
            risk_monitor=risk_monitor,
            order_manager=order_manager,
            fill_processor=fill_processor
        )
        self.components['portfolio'] = portfolio

    def _prepare_data(self) -> pd.DataFrame | None:
        """Подготавливает исторические данные, делегируя расчеты стратегии."""
        logger.info("Начало этапа подготовки данных...")
        strategy: BaseStrategy = self.components['strategy']
        data_slice = self.settings.get("data_slice")

        if data_slice is not None:
            raw_data = data_slice
        else:
            data_handler = HistoricLocalDataHandler(
                exchange=self.settings["exchange"],
                instrument_id=self.settings["instrument"],
                interval_str=self.settings["interval"],
                data_path=self.settings.get("data_dir", PATH_CONFIG["DATA_DIR"])
            )
            raw_data = data_handler.load_raw_data()

        if raw_data is None or raw_data.empty:
            logger.error(f"Не удалось получить данные для бэктеста по инструменту {self.settings['instrument']}.")
            return None

        enriched_data = strategy.process_data(raw_data.copy())

        if len(enriched_data) < strategy.min_history_needed:
            logger.error(f"Ошибка: Недостаточно данных для запуска стратегии '{strategy.name}'. "
                         f"Требуется {strategy.min_history_needed}, доступно {len(enriched_data)}.")
            return None

        logger.info("Этап подготовки данных завершен.")
        return enriched_data

    def _run_event_loop(self, enriched_data: pd.DataFrame) -> None:
        """Запускает главный цикл обработки событий."""
        logger.info("Запуск основного цикла обработки событий...")

        events_queue = self.components['events_queue']
        portfolio = self.components['portfolio']
        strategy = self.components['strategy']
        execution_handler = self.components['execution_handler']
        instrument = self.settings['instrument']

        # Проходим по каждой свече в данных
        for _, current_candle in enriched_data.iterrows():
            # 1. Всегда сначала кладем в очередь событие о новой свече
            market_event = MarketEvent(
                timestamp=current_candle['time'],
                instrument=instrument,
                data=current_candle
            )
            events_queue.put(market_event)

            # 2. Обрабатываем все события, которые есть в очереди НА ДАННЫЙ МОМЕНТ
            while not events_queue.empty():
                try:
                    event = events_queue.get(block=False)
                except queue.Empty:
                    break # На всякий случай, если очередь опустеет между проверкой и get()

                try:
                    if isinstance(event, MarketEvent):
                        backtest_time_filter.set_sim_time(event.timestamp)
                        portfolio.update_market_price(event)
                        strategy.on_market_event(event)
                    elif isinstance(event, SignalEvent):
                        portfolio.on_signal(event)
                    elif isinstance(event, OrderEvent):
                        # Теперь current_candle всегда определен и актуален для этой итерации
                        execution_handler.execute_order(event, current_candle)
                    elif isinstance(event, FillEvent):
                        portfolio.on_fill(event)
                except Exception as e:
                    logger.error(f"Критическая ошибка при обработке события {type(event).__name__}: {e}", exc_info=True)
                    # Прерываем внутренний цикл и, возможно, внешний
                    return # Выходим из всего метода при критической ошибке

        backtest_time_filter.reset_sim_time()
        logger.info("Основной цикл завершен.")

    def run(self) -> Dict[str, Any]:
        """
        Запускает одну полную сессию бэктеста и возвращает результаты.
        :return: Словарь с результатами, включая DataFrame сделок, финальный капитал и обогащенные данные.
        """
        try:
            self._initialize_components()

            enriched_data = self._prepare_data()
            if enriched_data is None:
                raise ValueError("Data preparation failed, no data returned.")

            self._run_event_loop(enriched_data)

            portfolio: Portfolio = self.components["portfolio"]
            trades_df = pd.DataFrame(portfolio.state.closed_trades) if portfolio.state.closed_trades else pd.DataFrame()

            return {
                "status": "success",
                "trades_df": trades_df,
                "final_capital": portfolio.state.current_capital,
                "initial_capital": self.settings["initial_capital"],
                "enriched_data": enriched_data,
                "open_positions": portfolio.state.positions
            }
        except Exception as e:
            logger.error(f"BacktestEngine столкнулся с ошибкой: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "trades_df": pd.DataFrame(),
                "final_capital": self.settings.get("initial_capital", 0),
                "initial_capital": self.settings.get("initial_capital", 0),
                "enriched_data": pd.DataFrame(),
                "open_positions": {}
            }