"""
Движок исполнения сигналов (Live Signal Engine).

Этот модуль отвечает за управление жизненным циклом торговых стратегий
в реальном времени. Он реализует паттерн "Orchestrator", который следит
за конфигурацией в БД и динамически запускает или останавливает стратегии.

Основные задачи:
1.  **Hot Reload:** Автоматическое обновление списка запущенных ботов без перезагрузки.
2.  **Isolation:** Запуск каждой стратегии в отдельной асинхронной задаче (Task).
3.  **Bridge:** Связывание потока данных (Feed), логики стратегии и шины сигналов (Bus).
4.  **Concurrency:** Безопасное выполнение синхронной математики стратегий в ThreadPoolExecutor.
5.  **Self-Healing:** Автоматический перезапуск упавших стратегий.
"""

import asyncio
import queue
import logging
from typing import Dict, Callable, Awaitable, List

from app.shared.events import SignalEvent
from app.core.interfaces import IPublisher, IDataFeed
from app.strategies.base_strategy import BaseStrategy
from app.core.portfolio.state import PortfolioState
from app.shared.time_helper import parse_interval_to_timedelta
from app.shared.config import config

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    Асинхронный движок управления стратегиями.

    Хранит реестр активных задач и периодически синхронизирует его
    с состоянием базы данных. Обеспечивает перезапуск стратегий в случае сбоев.

    Attributes:
        bus (IPublisher): Шина событий для отправки сигналов.
        _active_tasks (Dict[int, asyncio.Task]): Карта запущенных задач {config_id: Task}.
        _running (bool): Флаг работы главного цикла.
    """

    def __init__(self, bus: IPublisher):
        """
        Инициализирует движок.

        Args:
            bus (IPublisher): Адаптер шины событий для публикации сигналов.
        """
        self.bus = bus
        self._active_tasks: Dict[int, asyncio.Task] = {}
        self._running = False

    async def _strategy_wrapper(self,
                                config_id: int,
                                feed: IDataFeed,
                                strategy: BaseStrategy,
                                state: PortfolioState):
        """
        Рабочая обертка (Wrapper) для одной торговой пары.

        Этот метод выполняется как отдельная `asyncio.Task`. Он инкапсулирует
        весь жизненный цикл одной стратегии: от загрузки истории до обработки стрима.

        Алгоритм:
        1.  **Warm-up:** Рассчитывает необходимую глубину истории и загружает её.
        2.  **Stream:** Запускает WebSocket-подключение в фоне.
        3.  **Loop:** Бесконечно ждет новые свечи, запускает стратегию и отправляет сигналы.

        Args:
            config_id (int): ID конфигурации из БД (для логирования).
            feed (IDataFeed): Инициализированный поток данных.
            strategy (BaseStrategy): Инициализированная стратегия.
            state (PortfolioState): Хранилище динамического состояния портфеля.

        Raises:
            asyncio.CancelledError: При штатной остановке задачи.
            Exception: При критических ошибках (пробрасывается для перезапуска).
        """
        stream_task = None
        try:
            # --- 1. Разогрев (Warm-up) ---
            # Рассчитываем, сколько дней истории нужно скачать.
            needed_candles = strategy.min_history_needed + 10
            interval_delta = parse_interval_to_timedelta(feed.interval)
            total_seconds_needed = interval_delta.total_seconds() * needed_candles
            # Переводим в дни с коэффициентом запаса 1.5
            days_needed = (total_seconds_needed / 86400) * 1.5
            days_to_load = max(1, int(days_needed + 0.9))

            await feed.warm_up(days=days_to_load)

            # --- 2. Запуск Стрима ---
            stream_queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            # Запускаем WebSocket/gRPC клиент как фоновую задачу.
            stream_task = loop.create_task(feed.start_stream(stream_queue, loop))

            logger.info(
                f"✅ [Engine] Started strategy #{config_id}: {strategy.name} on {feed.instrument}. "
                f"Positions restored: {len(state.positions)}"
            )

            # --- 3. Главный цикл обработки ---
            while True:
                # Ждем событие MarketEvent из вебсокета
                event = await stream_queue.get()
                candle_data = event.data

                # Обновляем данные в фиде. Возвращает True, если свеча закрылась.
                is_new = await feed.process_candle(candle_data)

                if is_new:
                    # ВАЖНО: Запускаем синхронную математику стратегии в отдельном потоке,
                    # чтобы не блокировать Event Loop.
                    await loop.run_in_executor(None, strategy.on_candle, feed)

                    # Bridge: Sync Queue -> Async Bus
                    # Забираем сигналы из синхронной очереди стратегии
                    try:
                        while True:
                            # get_nowait не блокирует поток
                            signal = strategy.events_queue.get_nowait()

                            if isinstance(signal, SignalEvent):
                                logger.info(f"🔥 SIGNAL: {signal.direction} {signal.instrument} ({strategy.name})")
                                await self.bus.publish(signal)

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
            # Пробрасываем ошибку, чтобы задача завершилась статусом 'done' с исключением.
            # Оркестратор увидит это и перезапустит стратегию.
            raise

    async def run_orchestrator(self,
                               config_loader_func: Callable[[], Awaitable[List]],
                               pair_builder_func: Callable[[any], Awaitable[tuple]]):
        """
        Главный цикл оркестрации (Watchdog).

        Периодически опрашивает базу данных и синхронизирует список запущенных задач
        с желаемым состоянием. Также следит за "здоровьем" задач и перезапускает упавшие.

        Args:
            config_loader_func (Callable): Асинхронная функция, возвращающая список активных конфигов из БД.
            pair_builder_func (Callable): Асинхронная фабрика, возвращающая триплет (Feed, Strategy, State).
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
                        # Задача завершилась. Проверяем причину.
                        try:
                            exc = task.exception()
                            if exc:
                                logger.error(f"💀 Strategy #{cid} CRASHED: {exc}. Scheduling restart.")
                            else:
                                logger.warning(f"💀 Strategy #{cid} stopped unexpectedly (clean exit).")
                        except asyncio.CancelledError:
                            pass # Это нормальная остановка, не считаем ошибкой

                        dead_ids.append(cid)

                # Удаляем мертвые задачи из реестра.
                # Это ключевой момент для перезапуска: так как ID удален из _active_tasks,
                # но все еще присутствует в БД (db_configs), он попадет в ids_to_add на шаге 4.
                for cid in dead_ids:
                    self._active_tasks.pop(cid)

                # --- 2. Синхронизация с БД ---
                try:
                    db_configs = await config_loader_func()
                except Exception as e:
                    logger.error(f"DB Error fetching configs: {e}")
                    # Если БД недоступна, ждем и пробуем снова, не ломая работающие стратегии
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
                    # Мы не ждем завершения задачи здесь (await), чтобы не блокировать цикл.
                    # Python GC почистит ресурсы, а finally блок в wrapper закроет сокеты.

                # --- 4. Запуск новых (и перезапуск упавших) стратегий ---
                ids_to_add = target_ids - current_ids
                for cid in ids_to_add:
                    strat_config = db_config_map[cid]
                    try:
                        logger.info(f"🛠️ Building strategy #{cid} ({strat_config.instrument})...")

                        # Создаем объекты
                        feed, strategy, state = await pair_builder_func(strat_config)

                        # Запускаем задачу
                        task = asyncio.create_task(self._strategy_wrapper(cid, feed, strategy, state))
                        self._active_tasks[cid] = task

                    except Exception as e:
                        logger.error(f"❌ Failed to start strategy #{cid}: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"💥 Orchestrator loop critical error: {e}", exc_info=True)

            # Пауза перед следующей проверкой
            await asyncio.sleep(check_interval)

    async def stop(self):
        """
        Останавливает оркестратор и все дочерние задачи.
        Корректно завершает работу при выходе из приложения.
        """
        self._running = False
        logger.info("SignalEngine: Stopping all strategies...")

        # Отменяем все задачи
        for task in self._active_tasks.values():
            task.cancel()

        # Ждем их фактического завершения
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks.values(), return_exceptions=True)

        self._active_tasks.clear()