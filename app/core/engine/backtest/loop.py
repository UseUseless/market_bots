"""
Модуль основного цикла бэктеста (Backtest Engine).

Отвечает за оркестрацию процесса тестирования одной стратегии на одном инструменте.
Этот класс собирает все компоненты (Стратегия, Портфель, Риск-менеджер, Исполнение)
и запускает цикл обработки исторических данных свеча за свечой.
"""

import queue
import logging
import pandas as pd
from typing import Dict, Any, Optional

from app.shared.events import MarketEvent, SignalEvent, OrderEvent, FillEvent
from app.core.portfolio.state import PortfolioState
from app.shared.schemas import StrategyConfigModel
from app.core.portfolio.manager import Portfolio
from app.infrastructure.feeds.local import HistoricLocalDataHandler
from app.core.execution.simulator import SimulatedExecutionHandler
from app.core.risk.sizer import FixedRiskSizer
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.core.risk.monitor import RiskMonitor
from app.core.execution.order_logic import OrderManager
from app.core.portfolio.accounting import FillProcessor
from app.core.engine.backtest.feeds import BacktestDataFeed
from app.core.calculations.indicators import FeatureEngine

from app.strategies.base_strategy import BaseStrategy
from app.shared.logging_setup import backtest_time_filter
from app.infrastructure.storage.file_io import load_instrument_info
from app.shared.config import config

logger = logging.getLogger('backtester')


class BacktestEngine:
    """
    Движок одиночного бэктеста.

    Управляет жизненным циклом симуляции:
    1.  Загрузка данных.
    2.  Инициализация компонентов (Dependency Injection).
    3.  Запуск событийного цикла (Event Loop).
    4.  Сбор результатов.

    Attributes:
        settings (Dict): Конфигурация запуска (инструмент, таймфрейм, стратегия).
        events_queue (queue.Queue): Очередь событий для коммуникации компонентов.
        feature_engine (FeatureEngine): Сервис для расчета индикаторов.
        components (Dict): Реестр созданных объектов (portfolio, strategy и т.д.).
        pending_strategy_order (Optional[OrderEvent]): Буфер для отложенного исполнения
            рыночного ордера (симуляция задержки исполнения на 1 тик).
    """

    def __init__(self, settings: Dict[str, Any], events_queue: queue.Queue, feature_engine: FeatureEngine):
        """
        Создает экземпляр движка.

        Args:
            settings (Dict[str, Any]): Словарь с параметрами теста.
            events_queue (queue.Queue): Очередь событий.
            feature_engine (FeatureEngine): Инстанс движка индикаторов.
        """
        self.settings = settings
        self.events_queue = events_queue
        self.feature_engine = feature_engine

        self.components: Dict[str, Any] = {}
        # Ордер, сгенерированный стратегией на свече T, исполняется на открытии T+1
        self.pending_strategy_order: Optional[Any] = None

    def _initialize_components(self) -> None:
        """
        Собирает архитектуру приложения (Composition Root).

        Создает экземпляры всех необходимых классов (Strategy, Portfolio, RiskManager и т.д.)
        и связывает их друг с другом через внедрение зависимостей (DI).
        """
        logger.info("Инициализация компонентов бэктеста...")
        events_queue = self.events_queue
        self.components['events_queue'] = events_queue

        data_dir = self.settings.get("data_dir", config.PATH_CONFIG["DATA_DIR"])

        # Загрузка метаданных инструмента (шаг цены, лотность)
        instrument_info = load_instrument_info(
            exchange=self.settings["exchange"],
            instrument=self.settings["instrument"],
            interval=self.settings["interval"],
            data_dir=data_dir
        )

        # Инициализация стратегии
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

        strategy = strategy_class(
            events_queue=self.events_queue,
            feature_engine=self.feature_engine,
            config=strategy_config
        )
        self.components['strategy'] = strategy

        # Инициализация компонентов портфеля
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

        slippage_conf = config.BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {})

        execution_handler = SimulatedExecutionHandler(
            events_queue,
            commission_rate=self.settings["commission_rate"],
            slippage_config=slippage_conf
        )
        self.components['execution_handler'] = execution_handler

        portfolio_state = PortfolioState(initial_capital=self.settings["initial_capital"])

        # Сборка фасада портфеля
        portfolio = Portfolio(
            events_queue=events_queue,
            portfolio_state=portfolio_state,
            risk_monitor=risk_monitor,
            order_manager=order_manager,
            fill_processor=fill_processor
        )
        self.components['portfolio'] = portfolio

    def _prepare_data(self) -> pd.DataFrame | None:
        """
        Загружает и подготавливает исторические данные.

        1. Загружает сырые данные из файла или принимает срез (для WFO).
        2. Запускает `strategy.process_data` для векторного расчета индикаторов.
        3. Проверяет достаточность длины истории.

        Returns:
            pd.DataFrame: Обогащенные данные, готовые к симуляции.
        """
        logger.info("Начало этапа подготовки данных...")
        strategy: BaseStrategy = self.components['strategy']

        # Если передан готовый срез данных (например, из оптимизатора), используем его
        data_slice = self.settings.get("data_slice")

        if data_slice is not None:
            raw_data = data_slice
        else:
            data_path = self.settings.get("data_dir", config.PATH_CONFIG["DATA_DIR"])

            data_handler = HistoricLocalDataHandler(
                exchange=self.settings["exchange"],
                instrument_id=self.settings["instrument"],
                interval_str=self.settings["interval"],
                data_path=data_path
            )
            raw_data = data_handler.load_raw_data()

        if raw_data is None or raw_data.empty:
            logger.error(f"Не удалось получить данные для бэктеста по инструменту {self.settings['instrument']}.")
            return None

        # Векторный расчет индикаторов (оптимизация скорости)
        enriched_data = strategy.process_data(raw_data.copy())

        if len(enriched_data) < strategy.min_history_needed:
            logger.error(f"Ошибка: Недостаточно данных для запуска стратегии '{strategy.name}'. "
                         f"Требуется {strategy.min_history_needed}, доступно {len(enriched_data)}.")
            return None

        logger.info("Этап подготовки данных завершен.")
        return enriched_data

    def _process_queue(self, current_candle: pd.Series, phase: str):
        """
        Разбирает очередь событий до полного опустошения.

        Маршрутизирует события соответствующим обработчикам.

        Args:
            current_candle (pd.Series): Текущие рыночные данные.
            phase (str): Текущая фаза цикла (для отладки).
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
                # Если это рыночный ордер от стратегии, откладываем его до открытия следующей свечи
                if event.trigger_reason == 'SIGNAL':
                    self.pending_strategy_order = event
                else:
                    # Стоп-ордера исполняются немедленно (внутри этой же свечи)
                    execution_handler.execute_order(event, current_candle)

            elif isinstance(event, FillEvent):
                portfolio.on_fill(event)

    def _run_event_loop(self, enriched_data: pd.DataFrame) -> None:
        """
        Основной цикл симуляции (Event Loop).

        Итерируется по историческим данным, эмулируя ход времени.

        Последовательность действий на каждой свече:
        1.  **Phase 1 (Open):** Исполнение отложенных ордеров (вход по Open).
        2.  **Phase 2 (High/Low):** Проверка рисков (SL/TP внутри свечи).
        3.  **Phase 3 (Close):** Анализ стратегии (генерация сигналов по Close).

        Args:
            enriched_data (pd.DataFrame): Подготовленные данные.
        """
        logger.info("Запуск основного цикла обработки событий...")

        portfolio = self.components['portfolio']
        strategy = self.components['strategy']
        execution_handler = self.components['execution_handler']
        instrument = self.settings['instrument']

        # Инициализация фида данных
        feed = BacktestDataFeed(data=enriched_data, interval=self.settings['interval'])

        while feed.next():
            current_candle = feed.get_current_candle()

            # Создаем MarketEvent для обновления состояния портфеля
            market_event = MarketEvent(
                timestamp=current_candle['time'],
                instrument=instrument,
                data=current_candle
            )

            # Обновляем время в логгере
            backtest_time_filter.set_sim_time(market_event.timestamp)

            # --- ФАЗА 1: ИСПОЛНЕНИЕ (Начало бара) ---
            # Исполняем ордера, сгенерированные на закрытии ПРОШЛОЙ свечи.
            # Цена исполнения будет Open ТЕКУЩЕЙ свечи.
            if self.pending_strategy_order:
                execution_handler.execute_order(self.pending_strategy_order, current_candle)
                self.pending_strategy_order = None
                self._process_queue(current_candle, phase='EXECUTION')

            # --- ФАЗА 2: РИСКИ (Внутри бара) ---
            # Обновляем цену в портфеле и проверяем, не задеты ли SL/TP ценами High/Low.
            portfolio.update_market_price(market_event)
            self._process_queue(current_candle, phase='RISK')

            # --- ФАЗА 3: СТРАТЕГИЯ (Конец бара) ---
            # Анализируем данные по цене Close.
            strategy.on_candle(feed)

            # Если стратегия сгенерировала сигнал, он попадет в pending_strategy_order
            # после обработки очереди в _process_queue
            self._process_queue(current_candle, phase='STRATEGY')

        backtest_time_filter.reset_sim_time()
        logger.info("Основной цикл завершен.")

    def run(self) -> Dict[str, Any]:
        """
        Запускает процесс бэктеста от начала до конца.

        Returns:
            Dict[str, Any]: Результаты теста.
                - status: "success" / "error"
                - trades_df: Список сделок.
                - final_capital: Итоговый капитал.
                - enriched_data: Использованные данные (для отрисовки графиков).
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
            logger.error(
                f"BacktestEngine столкнулся с ошибкой на верхнем уровне для {self.settings['instrument']}: {e}",
                exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "trades_df": pd.DataFrame(),
                "final_capital": self.settings.get("initial_capital", 0),
                "initial_capital": self.settings.get("initial_capital", 0),
                "enriched_data": pd.DataFrame(),
                "open_positions": {}
            }