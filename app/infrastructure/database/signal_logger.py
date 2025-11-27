import asyncio
import logging

from app.core.event_bus import SignalBus
from app.shared.events import SignalEvent
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import SignalRepository

logger = logging.getLogger(__name__)

class DBLoggerAdapter:
    """
    Слушает шину событий и сохраняет все сигналы в базу данных.
    Нужен для истории и отображения в дэшборде.
    """
    def __init__(self, bus: SignalBus):
        self.bus = bus
        self.queue = None

    async def start(self):
        self.queue = self.bus.subscribe()
        logger.info("DBLogger: Listening and recording...")

        while True:
            try:
                event = await self.queue.get()
                if isinstance(event, SignalEvent):
                    await self._save_signal(event)
                self.queue.task_done()
            except asyncio.CancelledError:
                break

    async def _save_signal(self, event: SignalEvent):
        try:
            async with async_session_factory() as session:
                repo = SignalRepository(session)
                await repo.log_signal(event)
                # logger.debug(f"Signal saved to DB: {event.instrument}")
        except Exception as e:
            logger.error(f"Failed to save signal to DB: {e}")