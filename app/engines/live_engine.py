import asyncio
import logging
import os
import pandas as pd
from typing import Dict, Any
from asyncio import Queue as AsyncQueue
from functools import partial

from app.core.models.event import Event, MarketEvent, SignalEvent, OrderEvent, FillEvent
from app.core.models.portfolio_state import PortfolioState
from app.core.services.feature_engine import FeatureEngine
from app.core.portfolio import Portfolio
from app.core.risk.sizer import FixedRiskSizer
from app.core.services.risk_monitor import RiskMonitor
from app.core.services.order_manager import OrderManager
from app.core.services.fill_processor import FillProcessor

from app.core.services.data_feed import DataFeedService
from app.core.execution.notifier import NotifierExecutionHandler
from app.core.execution.live import LiveExecutionHandler

from app.core.data.stream_handlers import TinkoffStreamDataHandler, BybitStreamDataHandler, BaseStreamDataHandler
from app.utils.clients.tinkoff import TinkoffHandler
from app.utils.clients.bybit import BybitHandler
from app.utils.clients.abc import BaseDataClient

from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS

from config import LIVE_TRADING_CONFIG, PATH_CONFIG

logger = logging.getLogger(__name__)


class LiveEngine:
    """
    Движок для работы в реальном времени.
    Поддерживает режим 'SIGNAL_ONLY' (Монитор) и задел под 'TRADE_REAL'.
    """

    def __init__(self, settings: Dict[str, Any], events_queue: AsyncQueue[Event]):
        self.settings = settings
        self.loop = None
        self.events_queue = events_queue

        self.trade_mode = settings.get("trade_mode", "SIGNAL_ONLY")
        self.is_trading_enabled = self.trade_mode in ["REAL", "SANDBOX"]

        self.data_client: BaseDataClient | None = None
        self.portfolio: Portfolio | None = None
        self.strategy = None

        self.data_feed: DataFeedService | None = None

        self.data_handler: BaseStreamDataHandler | None = None
        self.execution_handler = None
        self.tasks = []

    async def run(self):
        """Главный метод запуска сессии."""
        self.loop = asyncio.get_running_loop()

        logger.info(f"--- Запуск Live Engine в режиме '{self.trade_mode}' ---")
        try:
            await self._initialize_components()
            await self._warm_up_data()

            logger.info("Запуск основных задач: стриминг данных и цикл обработки событий.")
            data_task = self.loop.create_task(self.data_handler.stream_data())
            loop_task = self.loop.create_task(self._main_event_loop())
            self.tasks = [data_task, loop_task]

            logger.info("Live Engine запущен. Ожидание рыночных данных...")
            await asyncio.gather(*self.tasks)

        except asyncio.CancelledError:
            logger.info("Задачи отменены. Завершение работы...")
        except Exception as e:
            logger.critical(f"Критическая ошибка в Live Engine: {e}", exc_info=True)
        finally:
            self.stop()

    async def _initialize_components(self):
        """Инициализация (DI контейнер)."""
        exchange = self.settings['exchange']
        instrument = self.settings['instrument']
        interval = self.settings['interval']
        category = self.settings.get("category", "linear")
        strategy_class = AVAILABLE_STRATEGIES[self.settings['strategy']]
        rm_class = AVAILABLE_RISK_MANAGERS[self.settings['risk_manager_type']]

        # 1. Клиент данных
        if exchange == 'tinkoff':
            self.data_client = TinkoffHandler(trade_mode="SANDBOX")
        elif exchange == 'bybit':
            self.data_client = BybitHandler(trade_mode="REAL")
        else:
            raise ValueError(f"Неподдерживаемая биржа: {exchange}")

        # 2. Информация об инструменте (Используем partial для kwargs!)
        try:
            # Оборачиваем вызов с именованными аргументами в partial
            get_info_func = partial(
                self.data_client.get_instrument_info,
                instrument,
                category=category
            )
            instrument_info = await self.loop.run_in_executor(None, get_info_func)
        except Exception as e:
            logger.error(f"Ошибка получения инфо инструмента: {e}")
            instrument_info = None

        if not instrument_info:
            logger.warning(f"Не удалось получить инфо для {instrument}. Использую дефолт.")
            instrument_info = {"min_order_qty": 1.0, "qty_step": 1.0, "lot_size": 1}

        # 3. Исполнитель
        class AsyncQueuePutter:
            def __init__(self, q: AsyncQueue, loop: asyncio.AbstractEventLoop):
                self._q, self._loop = q, loop

            def put(self, item):
                asyncio.run_coroutine_threadsafe(self._q.put(item), self._loop)

        sync_queue = AsyncQueuePutter(self.events_queue, self.loop)

        if self.is_trading_enabled:
            self.execution_handler = LiveExecutionHandler(self.events_queue, exchange, self.trade_mode, self.loop)
            initial_capital = 100000.0
        else:
            self.execution_handler = NotifierExecutionHandler(sync_queue)
            initial_capital = 100000.0

            # 4. Стрим данных
        if exchange == 'tinkoff':
            self.data_handler = TinkoffStreamDataHandler(self.events_queue, instrument, interval)
        else:
            self.data_handler = BybitStreamDataHandler(
                self.events_queue, instrument, interval, self.loop,
                channel_type=category,
                testnet=False
            )

        # 5. Ядро
        feature_engine = FeatureEngine()
        strategy_params = strategy_class.get_default_params()
        rm_params = rm_class.get_default_params()

        self.strategy = strategy_class(
            sync_queue, instrument, strategy_params, feature_engine,
            self.settings['risk_manager_type'], rm_params
        )

        self.data_feed = DataFeedService(
            feature_engine=feature_engine,
            required_indicators=self.strategy.required_indicators,
            max_len=500
        )

        # 6. Портфель
        risk_manager = rm_class(params=rm_params)
        position_sizer = FixedRiskSizer()
        risk_monitor = RiskMonitor(sync_queue)
        order_manager = OrderManager(sync_queue, risk_manager, position_sizer, instrument_info)

        log_filename = f"{self.trade_mode.lower()}_{instrument}_signals.jsonl"
        log_path = os.path.join(PATH_CONFIG["LOGS_LIVE_DIR"], log_filename)

        fill_processor = FillProcessor(
            trade_log_file=log_path, exchange=exchange, interval=interval,
            strategy_name=self.strategy.name, risk_manager_name=risk_manager.__class__.__name__,
            risk_manager_params=rm_params
        )

        portfolio_state = PortfolioState(initial_capital)
        self.portfolio = Portfolio(
            sync_queue, portfolio_state, risk_monitor, order_manager, fill_processor
        )

    async def _warm_up_data(self):
        """Загружает историю для разогрева индикаторов."""
        min_bars = self.strategy.min_history_needed
        bars_to_load = max(min_bars * 2, 300)
        days_to_load = 3
        category = self.settings.get("category", "linear")

        logger.info(f"Разогрев данных: Загрузка истории за {days_to_load} дн. (~{bars_to_load} баров)...")

        # Используем partial и здесь, чтобы передать category
        get_hist_func = partial(
            self.data_client.get_historical_data,
            self.settings['instrument'],
            self.settings['interval'],
            days_to_load,
            category=category
        )

        historical_data = await self.loop.run_in_executor(None, get_hist_func)

        if historical_data.empty:
            logger.warning("Не удалось загрузить историю! Индикаторы начнут считаться с нуля.")
            return

        self.data_feed.warm_up(historical_data)
        logger.info(f"Разогрев завершен. В памяти {len(self.data_feed._buffer)} свечей.")

    async def _main_event_loop(self):
        """Главный цикл обработки."""
        while True:
            event = await self.events_queue.get()
            try:
                if isinstance(event, MarketEvent):
                    candle_time = event.timestamp.strftime('%H:%M:%S')
                    candle_info = event.data
                    log_msg = (
                        f"📊 {event.instrument:<8} | {candle_time} | "
                        f"O: {candle_info['open']:<8} H: {candle_info['high']:<8} L: {candle_info['low']:<8} C: {candle_info['close']:<8} | "
                        f"Vol: {int(candle_info['volume'])}"
                    )
                    logger.info(log_msg)

                    self.portfolio.update_market_price(event)
                    data_window = self.data_feed.add_candle_and_get_window(event.data)

                    if data_window is not None:
                        event.data = data_window
                        self.strategy.on_market_event(event)
                    else:
                        logger.debug("Недостаточно данных для расчета индикаторов.")

                elif isinstance(event, SignalEvent):
                    self.portfolio.on_signal(event)

                elif isinstance(event, OrderEvent):
                    self.execution_handler.execute_order(event)

                elif isinstance(event, FillEvent):
                    self.portfolio.on_fill(event)

            except Exception as e:
                logger.error(f"Ошибка в цикле событий: {e}", exc_info=True)
            finally:
                self.events_queue.task_done()

    def stop(self):
        logger.info("Остановка Live Engine...")
        if self.execution_handler and hasattr(self.execution_handler, 'stop'):
            self.execution_handler.stop()
        for task in self.tasks:
            if not task.done():
                task.cancel()