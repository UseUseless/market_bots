import asyncio
import queue
import logging
from typing import Dict, Callable, Awaitable

from app.shared.events import SignalEvent
from app.core.interfaces import IPublisher

logger = logging.getLogger(__name__)


class SignalEngine:
    """
    Движок с поддержкой Hot Reload.
    Управляет жизненным циклом стратегий динамически.
    """

    def __init__(self, bus: IPublisher):
        self.bus = bus
        # Словарь: { strategy_config_id : asyncio.Task }
        self._active_tasks: Dict[int, asyncio.Task] = {}
        self._running = False

    async def _strategy_wrapper(self, config_id: int, feed, strategy):
        """
        Обертка для запуска одной пары.
        Содержит цикл обработки свечей.
        """
        try:
            # 1. Разогрев
            # Рассчитываем, сколько дней истории нужно стратегии
            # Например, если нужно 200 свечей по 5 минут = 1000 минут ~= 0.7 дня.
            # Берем с запасом (x2), но минимум 1 день.

            needed_candles = strategy.min_history_needed + 10

            # Грубый перевод интервалов в минуты
            interval_mins_map = {
                "1min": 1, "3min": 3, "5min": 5, "15min": 15, "30min": 30, "1hour": 60, "2hour": 120,
                "4hour": 240, "6hour": 360, "12hour": 720, "1day": 1440, "1week": 10080, "1month": 40320,
            }

            # Получаем множитель, если интервал неизвестен - считаем как 1 мин
            mins_per_candle = interval_mins_map.get(feed.interval, 1)

            total_minutes_needed = needed_candles * mins_per_candle
            days_needed = (total_minutes_needed / 1440) * 1.5  # Коэффициент запаса

            days_to_load = max(1, int(days_needed + 0.9))  # Округляем вверх, минимум 1 день

            await feed.warm_up(days=days_to_load)

            # 2. Стрим
            stream_queue = asyncio.Queue()
            loop = asyncio.get_running_loop()
            # Стрим запускаем как под-задачу. Если wrapper отменят, стрим тоже умрет.
            stream_task = loop.create_task(feed.start_stream(stream_queue, loop))

            logger.info(f"✅ [Engine] Started strategy #{config_id}: {strategy.name} on {feed.instrument}")

            # 3. Цикл
            while True:
                event = await stream_queue.get()
                candle_data = event.data

                is_new = await feed.process_candle(candle_data)

                if is_new:
                    # Важно: BaseStrategy.on_candle теперь синхронный метод.
                    # Чтобы не блокировать Event Loop тяжелыми расчетами, запускаем в executor.
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, strategy.on_candle, feed)

                    # Bridge Sync -> Async
                    try:
                        while True:
                            signal = strategy.events_queue.get_nowait()
                            if isinstance(signal, SignalEvent):
                                logger.info(f"🔥 SIGNAL: {signal.direction} {signal.instrument}")
                                await self.bus.publish(signal)
                            strategy.events_queue.task_done()
                    except queue.Empty:
                        pass

        except asyncio.CancelledError:
            logger.info(f"🛑 [Engine] Stopping strategy #{config_id}...")
            stream_task.cancel()
            raise
        except Exception as e:
            logger.error(f"⚠️ [Engine] Error in strategy #{config_id}: {e}", exc_info=True)
            await asyncio.sleep(5)  # Пауза перед рестартом при ошибке

    async def run_orchestrator(self,
                               config_loader_func: Callable[[], Awaitable[list]],
                               pair_builder_func: Callable[[any], Awaitable[tuple]]):
        """
        Главный цикл-менеджер (Watchdog).
        """
        self._running = True
        logger.info("🚀 Signal Engine Orchestrator started (Hot Reload enabled).")

        while self._running:
            try:
                # --- DEBUG START: Добавили лог ---
                logger.debug("🔄 [Orchestrator] Checking Database for updates...")
                # ---------------------------------

                # 1. Получаем актуальный список задач из БД
                db_configs = await config_loader_func()

                # --- DEBUG START: Смотрим, что пришло из базы ---
                logger.debug(f"📊 [Orchestrator] Found {len(db_configs)} active configs in DB.")
                for cfg in db_configs:
                    logger.debug(f"   -> ID: {cfg.id} | {cfg.instrument} | {cfg.strategy_name}")
                # ------------------------------------------------

                db_config_map = {cfg.id: cfg for cfg in db_configs}

                current_ids = set(self._active_tasks.keys())
                target_ids = set(db_config_map.keys())

                logger.debug(f"   -> Running IDs: {current_ids}")
                logger.debug(f"   -> Target IDs: {target_ids}")

                # 2. Вычисляем разницу
                ids_to_add = target_ids - current_ids
                ids_to_remove = current_ids - target_ids

                if ids_to_add:
                    logger.info(f"🆕 Finding new strategies to add: {ids_to_add}")

                # 3. Удаляем выключенные
                for cid in ids_to_remove:
                    task = self._active_tasks.pop(cid)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    logger.info(f"🗑️ Removed strategy #{cid}")

                # 4. Добавляем новые
                for cid in ids_to_add:
                    config = db_config_map[cid]
                    try:
                        logger.info(f"🛠️ Building strategy #{cid}...")
                        feed, strategy = await pair_builder_func(config)

                        task = asyncio.create_task(self._strategy_wrapper(cid, feed, strategy))
                        self._active_tasks[cid] = task
                        logger.info(f"✅ Strategy #{cid} launched successfully.")
                    except Exception as e:
                        logger.error(f"❌ Failed to start strategy #{cid}: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"💥 Orchestrator loop error: {e}", exc_info=True)

            # Пауза
            await asyncio.sleep(10)

    async def stop(self):
        self._running = False
        for task in self._active_tasks.values():
            task.cancel()