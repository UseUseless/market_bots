"""
Движок исполнения сигналов (Live Signal Engine).

Этот модуль отвечает за управление жизненным циклом торговых стратегий
в реальном времени. Он реализует паттерн "Orchestrator", который следит
за конфигурацией в БД и динамически запускает или останавливает стратегии.

Основные задачи:
1. **Hot Reload:** Автоматическое обновление списка запущенных ботов без перезагрузки.
2. **Isolation:** Запуск каждой стратегии в отдельной асинхронной задаче (Task).
3. **Bridge:** Связывание потока данных (Feed), логики стратегии и шины сигналов (Bus).
4. **Concurrency:** Безопасное выполнение синхронной математики стратегий в ThreadPoolExecutor.
"""

import asyncio
import queue
import logging
from typing import Dict, Callable, Awaitable, List

from app.shared.events import SignalEvent
from app.core.interfaces import IPublisher, IDataFeed
from app.strategies.base_strategy import BaseStrategy
from app.shared.time_helper import parse_interval_to_timedelta
from app.shared.config import config

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    Асинхронный движок управления стратегиями.

    Хранит реестр активных задач и периодически синхронизирует его
    с состоянием базы данных.

    Attributes:
        bus (IPublisher): Шина событий для отправки сигналов.
        _active_tasks (Dict[int, asyncio.Task]): Карта {config_id: Task}.
        _running (bool): Флаг работы главного цикла.
    """

    def __init__(self, bus: IPublisher):
        self.bus = bus
        self._active_tasks: Dict[int, asyncio.Task] = {}
        self._running = False

    async def _strategy_wrapper(self, config_id: int, feed: IDataFeed, strategy: BaseStrategy):
        """
        Рабочая обертка (Wrapper) для одной торговой пары.

        Этот метод выполняется как отдельная `asyncio.Task`. Он инкапсулирует
        весь жизненный цикл одной стратегии: от загрузки истории до обработки стрима.

        Алгоритм:
        1. **Warm-up:** Рассчитывает необходимую глубину истории и загружает её.
        2. **Stream:** Запускает WebSocket-подключение в фоне.
        3. **Loop:** Бесконечно ждет новые свечи, запускает стратегию и отправляет сигналы.

        Args:
            config_id (int): ID конфигурации из БД (для логирования).
            feed (IDataFeed): Инициализированный поток данных.
            strategy (BaseStrategy): Инициализированная стратегия.
        """
        stream_task = None
        try:
            # --- 1. Разогрев (Warm-up) ---
            # Рассчитываем, сколько дней истории нужно скачать.
            # Берем минимальное требование стратегии + запас.
            needed_candles = strategy.min_history_needed + 10

            # Используем хелпер для получения timedelta интервала (например, 5 минут)
            interval_delta = parse_interval_to_timedelta(feed.interval)

            # Считаем общее время в секундах
            total_seconds_needed = interval_delta.total_seconds() * needed_candles

            # Переводим в дни с коэффициентом запаса 1.5 (на случай выходных/праздников)
            days_needed = (total_seconds_needed / 86400) * 1.5
            days_to_load = max(1, int(days_needed + 0.9))  # Округляем вверх, минимум 1 день

            await feed.warm_up(days=days_to_load)

            # --- 2. Запуск Стрима ---
            stream_queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            # Запускаем WebSocket/gRPC клиент как фоновую задачу.
            # Если wrapper будет отменен, мы должны будем отменить и эту задачу.
            stream_task = loop.create_task(feed.start_stream(stream_queue, loop))

            logger.info(f"✅ [Engine] Started strategy #{config_id}: {strategy.name} on {feed.instrument}")

            # --- 3. Главный цикл обработки ---
            while True:
                # Ждем событие MarketEvent из вебсокета
                event = await stream_queue.get()
                candle_data = event.data

                # Обновляем данные в фиде (добавляем в DataFrame)
                # Возвращает True, если свеча новая (закрылась)
                is_new = await feed.process_candle(candle_data)

                if is_new:
                    # ВАЖНО: Математика стратегий (pandas, ta-lib) — синхронная и тяжелая.
                    # Чтобы не блокировать Event Loop (и не тормозить других ботов),
                    # запускаем расчет стратегии в ThreadPoolExecutor.
                    await loop.run_in_executor(None, strategy.on_candle, feed)

                    # Bridge: Sync Queue -> Async Bus
                    # Забираем сигналы из синхронной очереди стратегии и отправляем в асинхронную шину
                    try:
                        while True:
                            signal = strategy.events_queue.get_nowait()
                            if isinstance(signal, SignalEvent):
                                logger.info(f"🔥 SIGNAL: {signal.direction} {signal.instrument} ({strategy.name})")
                                await self.bus.publish(signal)
                            strategy.events_queue.task_done()
                    except queue.Empty:
                        pass

        except asyncio.CancelledError:
            logger.info(f"🛑 [Engine] Stopping strategy #{config_id}...")

            # Корректное завершение стрима
            if stream_task and not stream_task.done():
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass  # Ожидаемое поведение
            raise

        except Exception as e:
            logger.error(f"⚠️ [Engine] Error in strategy #{config_id}: {e}", exc_info=True)
            # Небольшая пауза перед перезапуском (если оркестратор решит перезапустить)
            await asyncio.sleep(5)

    async def run_orchestrator(self,
                               config_loader_func: Callable[[], Awaitable[List]],
                               pair_builder_func: Callable[[any], Awaitable[tuple]]):
        """
        Главный цикл оркестрации (Watchdog).

        Периодически опрашивает базу данных и синхронизирует список запущенных задач
        с желаемым состоянием (Hot Reload).

        Args:
            config_loader_func: Асинхронная функция, возвращающая список активных конфигов из БД.
            pair_builder_func: Асинхронная фабрика, возвращающая пару (Feed, Strategy).
        """
        self._running = True
        # Интервал проверки обновлений в БД (секунды)
        check_interval = config.LIVE_TRADING_CONFIG.get("LIVE_RECONNECT_DELAY_SECONDS", 10)

        logger.info(f"🚀 Signal Engine Orchestrator started. Check interval: {check_interval}s.")

        while self._running:
            try:
                # 1. Получаем актуальный список задач из БД
                db_configs = await config_loader_func()
                db_config_map = {cfg.id: cfg for cfg in db_configs}

                current_ids = set(self._active_tasks.keys())
                target_ids = set(db_config_map.keys())

                # 2. Вычисляем разницу (Set difference)
                ids_to_add = target_ids - current_ids
                ids_to_remove = current_ids - target_ids

                # 3. Удаляем выключенные стратегии
                for cid in ids_to_remove:
                    logger.info(f"Stopping strategy #{cid} (Removed from DB/Disabled)...")
                    task = self._active_tasks.pop(cid)
                    task.cancel()
                    # Ждем фактической остановки, чтобы освободить ресурсы
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    logger.info(f"🗑️ Strategy #{cid} stopped.")

                # 4. Запускаем новые стратегии
                for cid in ids_to_add:
                    strat_config = db_config_map[cid]
                    try:
                        logger.info(f"🛠️ Building strategy #{cid} ({strat_config.instrument})...")
                        feed, strategy = await pair_builder_func(strat_config)

                        # Создаем Task и сохраняем ссылку
                        task = asyncio.create_task(self._strategy_wrapper(cid, feed, strategy))
                        self._active_tasks[cid] = task
                    except Exception as e:
                        logger.error(f"❌ Failed to start strategy #{cid}: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"💥 Orchestrator loop error: {e}", exc_info=True)

            # Пауза перед следующей проверкой БД
            await asyncio.sleep(check_interval)

    async def stop(self):
        """Останавливает оркестратор и все дочерние задачи."""
        self._running = False
        logger.info("SignalEngine: Stopping all strategies...")

        for task in self._active_tasks.values():
            task.cancel()

        if self._active_tasks:
            await asyncio.gather(*self._active_tasks.values(), return_exceptions=True)

        self._active_tasks.clear()