import asyncio
import logging
from typing import Dict, Any
import os
import pandas as pd
from asyncio import Queue as AsyncQueue

from app.core.models.event import Event, MarketEvent, SignalEvent, OrderEvent, FillEvent
from app.core.portfolio import Portfolio
from app.core.risk.sizer import FixedRiskSizer
from app.core.services.risk_monitor import RiskMonitor
from app.core.services.order_manager import OrderManager
from app.core.services.fill_processor import FillProcessor

from app.core.data.stream_handlers import TinkoffStreamDataHandler, BybitStreamDataHandler, BaseStreamDataHandler
from app.core.execution.live import LiveExecutionHandler
from app.utils.clients.tinkoff import TinkoffHandler
from app.utils.clients.bybit import BybitHandler
from app.utils.clients.abc import BaseDataClient

from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS

from config import LIVE_TRADING_CONFIG, PATH_CONFIG

logger = logging.getLogger(__name__)


class LiveEngine:
    """
    Оркестратор для запуска одной торговой сессии в live-режиме или песочнице.
    Отвечает за инициализацию всех компонентов, управление asyncio задачами
    и корректное завершение работы.
    """

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.loop = asyncio.get_running_loop()
        self.events_queue: AsyncQueue[Event] = AsyncQueue()

        # Атрибуты, которые будут инициализированы
        self.data_client: BaseDataClient | None = None
        self.portfolio: Portfolio | None = None
        self.strategy = None
        self.data_handler: BaseStreamDataHandler | None = None
        self.execution_handler: LiveExecutionHandler | None = None
        self.tasks = []

    async def run(self):
        """Главный метод, запускающий live-сессию."""
        logger.info(f"--- Запуск Live Trading Engine в режиме '{self.settings['trade_mode']}' ---")
        try:
            await self._initialize_components()
            await self._prepare_initial_data()

            logger.info("Запуск основных задач: стриминг данных и цикл обработки событий.")
            data_task = self.loop.create_task(self.data_handler.stream_data())
            loop_task = self.loop.create_task(self._main_event_loop())
            self.tasks = [data_task, loop_task]

            logger.info("Live Engine успешно запущен. Для остановки нажмите Ctrl+C.")
            await asyncio.gather(*self.tasks)

        except asyncio.CancelledError:
            logger.info("Задачи были отменены. Завершение работы...")
        except Exception as e:
            logger.critical(f"Критическая ошибка в Live Engine: {e}", exc_info=True)
        finally:
            self.stop()

    async def _initialize_components(self):
        """Инициализирует все необходимые компоненты для live-торговли."""
        logger.info("Инициализация компонентов...")

        exchange = self.settings['exchange']
        instrument = self.settings['instrument']
        interval = self.settings['interval']
        trade_mode = self.settings['trade_mode']
        strategy_class = AVAILABLE_STRATEGIES[self.settings['strategy']]
        rm_class = AVAILABLE_RISK_MANAGERS[self.settings['risk_manager_type']]

        # --- 1. Создание клиентов API ---
        if exchange == 'tinkoff':
            self.data_client = TinkoffHandler(trade_mode=trade_mode)
        elif exchange == 'bybit':
            self.data_client = BybitHandler(trade_mode=trade_mode)
        else:
            raise ValueError(f"Неподдерживаемая биржа: {exchange}")

        # --- 2. Получение информации об инструменте и начального капитала ---
        instrument_info = await self.loop.run_in_executor(
            None, self.data_client.get_instrument_info, instrument
        )
        if not instrument_info:
            raise ConnectionError(f"Не удалось получить информацию об инструменте {instrument}.")

        # TODO: Реализовать получение реального баланса счета через API
        # Сейчас для песочницы используется фиксированное значение, что является упрощением.
        # Для реальной торговли здесь должен быть API-запрос баланса.
        initial_capital = 100000.0
        logger.info(f"Начальный капитал (установлен вручную): {initial_capital}")

        # --- 3. Создание обработчиков и стратегии ---
        self.execution_handler = LiveExecutionHandler(self.events_queue, exchange, trade_mode, self.loop)

        if exchange == 'tinkoff':
            self.data_handler = TinkoffStreamDataHandler(self.events_queue, instrument, interval)
        else:  # bybit
            self.data_handler = BybitStreamDataHandler(
                self.events_queue, instrument, interval, self.loop,
                channel_type=self.settings.get('category', 'linear'),
                testnet=(trade_mode == "SANDBOX")
            )

        # --- 4. Сборка ядра (Portfolio и его сервисы) ---
        # Создаем специальный "прокси" для очереди, чтобы из синхронных callback-ов (как в pybit)
        # можно было безопасно класть события в асинхронную очередь.
        class AsyncQueuePutter:
            def __init__(self, q: AsyncQueue, loop: asyncio.AbstractEventLoop):
                self._q, self._loop = q, loop

            def put(self, item):
                asyncio.run_coroutine_threadsafe(self._q.put(item), self._loop)

        sync_compatible_queue = AsyncQueuePutter(self.events_queue, self.loop)

        strategy_params = strategy_class.get_default_params()
        rm_params = rm_class.get_default_params()
        self.strategy = strategy_class(
            sync_compatible_queue, instrument, strategy_params,
            self.settings['risk_manager_type'], rm_params
        )

        risk_manager = rm_class(params=rm_params)
        position_sizer = FixedRiskSizer()
        risk_monitor = RiskMonitor(sync_compatible_queue)
        order_manager = OrderManager(sync_compatible_queue, risk_manager, position_sizer, instrument_info)

        log_path = os.path.join(PATH_CONFIG["LOGS_LIVE_DIR"], f"{trade_mode.lower()}_{instrument}.jsonl")

        fill_processor = FillProcessor(
            trade_log_file=log_path,
            exchange=exchange,
            interval=interval,
            strategy_name=self.strategy.name,
            risk_manager_name=risk_manager.__class__.__name__,
            risk_manager_params=rm_params
        )

        self.portfolio = Portfolio(
            sync_compatible_queue, initial_capital, risk_monitor, order_manager, fill_processor
        )
        logger.info("Все компоненты успешно инициализированы.")

    async def _prepare_initial_data(self):
        """Загружает и подготавливает начальный набор исторических данных для стратегии."""
        min_bars_needed = self.strategy.min_history_needed
        buffer_multiplier = LIVE_TRADING_CONFIG['LIVE_HISTORY_BUFFER_MULTIPLIER']
        bars_to_load = min_bars_needed * buffer_multiplier

        # Грубая оценка, сколько дней нужно загрузить, чтобы получить нужное кол-во свечей
        # (зависит от интервала, но для интрадей это будет с запасом)
        days_to_load = (bars_to_load * pd.Timedelta(self.settings['interval']).total_seconds()) / (24 * 3600)
        days_to_load = max(int(days_to_load) + 2, 2)  # +2 дня на всякий случай

        logger.info(
            f"Требуется {min_bars_needed} баров для старта. Загрузка ~{bars_to_load} баров ({days_to_load} дней) истории...")

        historical_data = await self.loop.run_in_executor(
            None, self.data_client.get_historical_data, self.settings['instrument'], self.settings['interval'],
            days_to_load
        )

        if historical_data.empty or len(historical_data) < min_bars_needed:
            raise ValueError(
                f"Недостаточно исторических данных для запуска. Требуется {min_bars_needed}, получено {len(historical_data)}.")

        enriched_data = self.strategy.process_data(historical_data)

        # "Прогреваем" историю стратегии, чтобы она была готова к первому событию
        for _, row in enriched_data.tail(min_bars_needed + 2).iterrows():
            self.strategy.data_history.append(row)

        logger.info(f"История стратегии успешно 'прогрета' {len(self.strategy.data_history)} барами.")

    async def _main_event_loop(self):
        """Главный цикл, который обрабатывает события из очереди."""
        logger.info("Основной цикл обработки событий запущен...")
        while True:
            event = await self.events_queue.get()
            try:
                if isinstance(event, MarketEvent):
                    self.portfolio.update_market_price(event)
                    self.strategy.on_market_event(event)
                elif isinstance(event, SignalEvent):
                    self.portfolio.on_signal(event)
                elif isinstance(event, OrderEvent):
                    # В live-режиме исполнение ордера - асинхронная операция
                    await self.execution_handler.execute_order(event)
                elif isinstance(event, FillEvent):
                    self.portfolio.on_fill(event)
            except Exception as e:
                logger.error(f"Ошибка при обработке события {type(event).__name__}: {e}", exc_info=True)
            finally:
                self.events_queue.task_done()

    def stop(self):
        """Корректно останавливает все компоненты."""
        logger.info("Начало остановки Live Engine...")
        if self.execution_handler:
            self.execution_handler.stop()

        for task in self.tasks:
            if not task.done():
                task.cancel()

        logger.info("Live Engine остановлен.")