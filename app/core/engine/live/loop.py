"""
Движок исполнения сигналов (Live Signal Engine).

Этот модуль отвечает за управление жизненным циклом торговых стратегий
в реальном времени. Он реализует паттерн "Orchestrator", который следит
за конфигурацией в БД и динамически запускает или останавливает стратегии.

Архитектура:
    Движок использует прямую композицию (Direct Composition) для обработки сигналов.
    Вместо сложной шины событий с ордерами, он реализует упрощенный пайплайн:
    DataFeed -> Strategy -> SignalEvent -> Handlers (Telegram, DB).
"""

import asyncio
import queue
import logging
from typing import Dict, Callable, Awaitable, List, Any

from app.shared.events import SignalEvent
from app.shared.interfaces import MarketDataProvider, SignalHandler
from app.strategies.base_strategy import BaseStrategy
from app.shared.time_helper import interval_to_timedelta
from app.shared.config import config

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    Асинхронный движок управления стратегиями.

    Хранит реестр активных задач и периодически синхронизирует его
    с состоянием базы данных. Обеспечивает маршрутизацию сигналов
    зарегистрированным обработчикам.

    Attributes:
        handlers (List[SignalHandler]): Список сервисов, которые должны получить сигнал
            (например, TelegramSender, DBSignalLogger).
        _active_tasks (Dict[int, asyncio.Task]): Карта запущенных задач {config_id: Task}.
        _running (bool): Флаг работы главного цикла.
    """

    def __init__(self, handlers: List[SignalHandler]):
        """
        Инициализирует движок.

        Args:
            handlers (List[SignalHandler]): Список обработчиков сигналов.
        """
        self.handlers = handlers
        self._active_tasks: Dict[int, asyncio.Task] = {}
        self._running = False

    async def _safe_handle(self, handler: SignalHandler, signal: SignalEvent) -> None:
        """
        Безопасно вызывает обработчик сигнала.

        Оборачивает вызов в try-except, чтобы исключение в одном обработчике
        (например, сбой сети при отправке в Telegram) не прерывало работу
        движка и других обработчиков.

        Args:
            handler (SignalHandler): Обработчик (Subscriber).
            signal (SignalEvent): Событие сигнала.
        """
        try:
            await handler.handle_signal(signal)
        except Exception as e:
            logger.error(
                f"Ошибка в обработчике {handler.__class__.__name__}: {e}",
                exc_info=True
            )

    async def _strategy_wrapper(self,
                                config_id: int,
                                feed: MarketDataProvider,
                                strategy: BaseStrategy) -> None:
        """
        Рабочая обертка (Wrapper) для одной торговой пары.

        Этот метод выполняется как отдельная `asyncio.Task`. Он инкапсулирует
        весь жизненный цикл одной стратегии: от загрузки истории до обработки стрима.

        Args:
            config_id (int): ID конфигурации из БД (для логирования).
            feed (MarketDataProvider): Инициализированный поток данных.
            strategy (BaseStrategy): Инициализированная стратегия.

        Raises:
            asyncio.CancelledError: При штатной остановке задачи.
            Exception: При критических ошибках (пробрасывается для перезапуска).
        """
        stream_task = None
        try:
            # --- 1. Разогрев (Warm-up) ---
            # Рассчитываем, сколько истории нужно скачать
            needed_candles = strategy.min_history_needed + 10
            interval_delta = interval_to_timedelta(feed.interval)

            # Watchdog: если данных нет 5 интервалов (или мин. 5 минут), считаем поток мертвым
            watchdog_timeout = max(300.0, interval_delta.total_seconds() * 5)

            total_seconds_needed = interval_delta.total_seconds() * needed_candles
            days_needed = (total_seconds_needed / 86400) * 1.5
            days_to_load = max(1, int(days_needed + 0.9))

            # Загружаем исторические данные
            await feed.warm_up(days=days_to_load)

            # --- 2. Запуск Стрима ---
            stream_queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            # Запускаем WebSocket/gRPC клиент как фоновую задачу
            stream_task = loop.create_task(feed.start_stream(stream_queue, loop))

            logger.info(
                f"✅ [Engine] Started strategy #{config_id}: {strategy.name} on {feed.instrument}. "
                f"Watchdog: {int(watchdog_timeout)}s"
            )

            # --- 3. Главный цикл обработки ---
            while True:
                try:
                    # Ждем событие с таймаутом (Watchdog)
                    event = await asyncio.wait_for(stream_queue.get(), timeout=watchdog_timeout)
                except asyncio.TimeoutError:
                    logger.error(
                        f"💀 [Engine] Strategy #{config_id}: No data for {int(watchdog_timeout)}s. "
                        f"Restarting stream..."
                    )
                    raise  # Перезапуск задачи через оркестратор

                candle_data = event.data

                # Обновляем данные в фиде (добавляем свечу в буфер, пересчитываем индикаторы)
                is_new = await feed.process_candle(candle_data)

                if is_new:
                    # Запускаем синхронную математику стратегии в ThreadPool,
                    # чтобы не блокировать основной AsyncIO Loop тяжелыми расчетами Pandas.
                    await loop.run_in_executor(None, strategy.on_candle, feed)

                    # Забираем сигналы из синхронной очереди стратегии
                    try:
                        while True:
                            # get_nowait не блокирует поток
                            signal = strategy.events_queue.get_nowait()

                            if isinstance(signal, SignalEvent):
                                logger.info(f"🔥 SIGNAL: {signal.direction} {signal.instrument} ({strategy.name})")

                                # Broadcast: Рассылаем сигнал всем обработчикам.
                                # Используем create_task для параллельного запуска (Fire-and-Forget).
                                for handler in self.handlers:
                                    asyncio.create_task(self._safe_handle(handler, signal))

                            strategy.events_queue.task_done()
                    except queue.Empty:
                        pass

        except asyncio.CancelledError:
            logger.info(f"🛑 [Engine] Stopping strategy #{config_id}...")
            if stream_task and not stream_task.done():
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
            raise

        except Exception as e:
            logger.error(f"⚠️ [Engine] Error in strategy #{config_id}: {e}", exc_info=True)
            raise

    async def run_orchestrator(self,
                               config_loader_func: Callable[[], Awaitable[List]],
                               pair_builder_func: Callable[[Any], Awaitable[tuple]]) -> None:
        """
        Главный цикл оркестрации (Watchdog).

        Периодически опрашивает базу данных и синхронизирует список запущенных задач
        с желаемым состоянием. Также следит за "здоровьем" задач и перезапускает упавшие.

        Args:
            config_loader_func (Callable): Асинхронная функция, возвращающая список конфигов из БД.
            pair_builder_func (Callable): Асинхронная фабрика, возвращающая (Feed, Strategy).
        """
        self._running = True
        check_interval = config.LIVE_TRADING_CONFIG.get("LIVE_RECONNECT_DELAY_SECONDS", 10)

        logger.info(f"🚀 Signal Engine Orchestrator started. Check interval: {check_interval}s.")

        while self._running:
            try:
                # --- 1. Проверка здоровья задач (Health Check) ---
                dead_ids = []
                for cid, task in list(self._active_tasks.items()):
                    if task.done():
                        try:
                            exc = task.exception()
                            if exc:
                                logger.error(f"💀 Strategy #{cid} CRASHED: {exc}. Scheduling restart.")
                            else:
                                logger.warning(f"💀 Strategy #{cid} stopped unexpectedly (clean exit).")
                        except asyncio.CancelledError:
                            pass
                        dead_ids.append(cid)

                for cid in dead_ids:
                    self._active_tasks.pop(cid)

                # --- 2. Синхронизация с БД ---
                try:
                    db_configs = await config_loader_func()
                except Exception as e:
                    logger.error(f"DB Error fetching configs: {e}")
                    await asyncio.sleep(check_interval)
                    continue

                db_config_map = {cfg.id: cfg for cfg in db_configs}

                current_ids = set(self._active_tasks.keys())
                target_ids = set(db_config_map.keys())

                # --- 3. Удаление выключенных стратегий ---
                ids_to_remove = current_ids - target_ids
                for cid in ids_to_remove:
                    logger.info(f"🛑 Stopping strategy #{cid} (Disabled in DB)...")
                    task = self._active_tasks.pop(cid)
                    task.cancel()

                # --- 4. Запуск новых (и перезапуск упавших) стратегий ---
                ids_to_add = target_ids - current_ids
                for cid in ids_to_add:
                    strat_config = db_config_map[cid]
                    try:
                        logger.info(f"🛠️ Building strategy #{cid} ({strat_config.instrument})...")

                        # Создаем объекты. pair_builder_func больше не возвращает state.
                        feed, strategy = await pair_builder_func(strat_config)

                        # Запускаем задачу без PortfolioState
                        task = asyncio.create_task(self._strategy_wrapper(cid, feed, strategy))
                        self._active_tasks[cid] = task

                    except Exception as e:
                        logger.error(f"❌ Failed to start strategy #{cid}: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"💥 Orchestrator loop critical error: {e}", exc_info=True)

            await asyncio.sleep(check_interval)

    async def stop(self) -> None:
        """
        Останавливает оркестратор и все дочерние задачи.
        """
        self._running = False
        logger.info("SignalEngine: Stopping all strategies...")

        for task in self._active_tasks.values():
            task.cancel()

        if self._active_tasks:
            await asyncio.gather(*self._active_tasks.values(), return_exceptions=True)

        self._active_tasks.clear()