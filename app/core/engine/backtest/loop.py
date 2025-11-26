import queue
import logging
import pandas as pd
from typing import Dict, Any, Optional

from app.shared.events import MarketEvent, SignalEvent, OrderEvent, FillEvent
from app.core.portfolio.state import PortfolioState
from app.shared.schemas import StrategyConfigModel
from app.core.portfolio.manager import Portfolio
from app.core.calculations.indicators import FeatureEngine
from app.infrastructure.feeds.local import HistoricLocalDataHandler
from app.services.execution import SimulatedExecutionHandler
from app.core.risk.sizer import FixedRiskSizer
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.core.risk.monitor import RiskMonitor
from app.core.execution.order_logic import OrderManager
from app.core.portfolio.accounting import FillProcessor
from app.core.engine.backtest.feeds import BacktestDataFeed


from app.strategies.base_strategy import BaseStrategy
from app.shared.logging_setup import backtest_time_filter
from app.infrastructure.storage.file_io import load_instrument_info
from config import PATH_CONFIG, BACKTEST_CONFIG

logger = logging.getLogger('backtester')

class BacktestEngine:
    """
    Оркестратор для запуска одной сессии бэктеста.
    Инкапсулирует всю логику инициализации компонентов, подготовки данных
    и запуска основного цикла обработки событий.
    """

    def __init__(self, settings: Dict[str, Any], events_queue: queue.Queue):
        """
        Инициализирует движок с заданной конфигурацией.

        :param settings: Словарь с полной конфигурацией для одного бэктеста.
        :param events_queue: Внешне созданная очередь событий.
        """
        self.settings = settings
        self.components: Dict[str, Any] = {}
        self.events_queue = events_queue
        self.pending_strategy_order: Optional[OrderEvent] = None

    def _initialize_components(self) -> None:
        """
        Приватный метод для инициализации и сборки всех компонентов системы.
        Реализует принцип Dependency Injection.
        """
        logger.info("Инициализация компонентов бэктеста...")
        events_queue = self.events_queue
        self.components['events_queue'] = events_queue

        instrument_info = load_instrument_info(
            exchange=self.settings["exchange"],
            instrument=self.settings["instrument"],
            interval=self.settings["interval"],
            data_dir=self.settings.get("data_dir", PATH_CONFIG["DATA_DIR"])
        )

        feature_engine = FeatureEngine()

        strategy_class = self.settings["strategy_class"]
        strategy_params = self.settings.get("strategy_params") or strategy_class.get_default_params()
        strategy_config = StrategyConfigModel(
            strategy_name=strategy_class.__name__,
            instrument=self.settings["instrument"],
            exchange=self.settings["exchange"],
            interval=self.settings["interval"],
            params=strategy_params,
            risk_manager_type=self.settings["risk_manager_type"],
            risk_manager_params=self.settings.get("risk_manager_params") or {}
        )

        # 2. Передаем его в стратегию
        strategy = strategy_class(
            events_queue=events_queue,
            feature_engine=feature_engine,
            config=strategy_config
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

        portfolio_state = PortfolioState(initial_capital=self.settings["initial_capital"])

        portfolio = Portfolio(
            events_queue=events_queue,
            portfolio_state=portfolio_state,
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

    def _process_queue(self, current_candle: pd.Series, phase: str):
        """
        Вспомогательный метод для обработки очереди событий.

        :param phase: 'EXECUTION' (начало бара) или 'STRATEGY' (конец бара).
                      В фазе EXECUTION мы исполняем SL/TP немедленно.
                      В фазе STRATEGY мы сохраняем сигналы на следующий бар.
        """
        events_queue = self.components['events_queue']
        portfolio = self.components['portfolio']
        execution_handler = self.components['execution_handler']

        while not events_queue.empty():
            try:
                event = events_queue.get(block=False)
            except queue.Empty:
                break

            if isinstance(event, SignalEvent):
                portfolio.on_signal(event)

            elif isinstance(event, OrderEvent):
                if event.trigger_reason == 'SIGNAL':
                    self.pending_strategy_order = event
                else:
                    execution_handler.execute_order(event, current_candle)

            elif isinstance(event, FillEvent):
                portfolio.on_fill(event)

    def _run_event_loop(self, enriched_data: pd.DataFrame) -> None:
        """
        Главный цикл симуляции.
        Использует BacktestDataFeed для эмуляции потока данных.
        """
        logger.info("Запуск основного цикла обработки событий...")

        portfolio = self.components['portfolio']
        strategy = self.components['strategy']
        execution_handler = self.components['execution_handler']
        instrument = self.settings['instrument']

        # 1. Инициализируем Фид
        feed = BacktestDataFeed(data=enriched_data, interval=self.settings['interval'])

        # 2. Крутим цикл, пока есть данные
        while feed.next():
            current_candle = feed.get_current_candle()

            # Создаем событие рынка для Портфеля и Риск-менеджера
            market_event = MarketEvent(
                timestamp=current_candle['time'],
                instrument=instrument,
                data=current_candle
            )

            backtest_time_filter.set_sim_time(market_event.timestamp)

            # ФАЗА 1: ИСПОЛНЕНИЕ ОТЛОЖЕННЫХ ОРДЕРОВ (Начало свечи, цена Open)
            if self.pending_strategy_order:
                execution_handler.execute_order(self.pending_strategy_order, current_candle)
                self.pending_strategy_order = None
                self._process_queue(current_candle, phase='EXECUTION')

            # ФАЗА 2: ПРОВЕРКА РИСКОВ (Внутри свечи, цены High/Low)
            portfolio.update_market_price(market_event)
            self._process_queue(current_candle, phase='EXECUTION')

            # ФАЗА 3: АНАЛИЗ СТРАТЕГИИ (Конец свечи, цена Close)
            # ВАЖНО: Теперь мы передаем стратегии фид, а не событие!
            strategy.on_candle(feed)

            # Если стратегия дала сигнал, он попадет в очередь.
            self._process_queue(current_candle, phase='STRATEGY')

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
            logger.error(f"BacktestEngine столкнулся с ошибкой на верхнем уровне для {self.settings['instrument']}: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "trades_df": pd.DataFrame(),
                "final_capital": self.settings.get("initial_capital", 0),
                "initial_capital": self.settings.get("initial_capital", 0),
                "enriched_data": pd.DataFrame(),
                "open_positions": {}
            }