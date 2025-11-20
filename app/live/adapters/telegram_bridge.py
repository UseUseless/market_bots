import asyncio
import logging
from datetime import datetime

from app.live.bus.signal_bus import SignalBus
from app.core.models.event import SignalEvent
from app.bots.manager import BotManager
from app.storage.database import async_session_factory
from app.storage.repositories import BotRepository, ConfigRepository
from app.storage.models import StrategyConfig

logger = logging.getLogger(__name__)


class TelegramBridge:
    def __init__(self, bus: SignalBus, bot_manager: BotManager):
        self.bus = bus
        self.bot_manager = bot_manager
        self.queue = None

    async def start(self):
        self.queue = self.bus.subscribe()
        logger.info("TelegramBridge: Listening...")

        while True:
            try:
                event = await self.queue.get()
                if isinstance(event, SignalEvent):
                    await self._process_signal(event)
                self.queue.task_done()
            except asyncio.CancelledError:
                break

    async def _process_signal(self, event: SignalEvent):
        """
        1. Найти в БД конфиг стратегии (по имени и инструменту).
        2. Узнать bot_id.
        3. Получить список chat_id подписчиков.
        4. Отправить через manager.
        """
        # Хак: пока мы не передаем ID конфига в событии, ищем по косвенным признакам
        # В идеале: SignalEvent должен содержать config_id

        async with async_session_factory() as session:
            # Ищем конфиг стратегии, чтобы понять, какому боту она принадлежит
            # ВНИМАНИЕ: Это упрощение. Если есть 2 одинаковые стратегии на разных ботах,
            # мы найдем обе или первую.
            from sqlalchemy import select
            query = select(StrategyConfig).where(
                StrategyConfig.strategy_name == event.strategy_id,
                StrategyConfig.instrument == event.instrument,
                StrategyConfig.is_active == True
            )
            result = await session.execute(query)
            configs = result.scalars().all()

            if not configs:
                return

            repo = BotRepository(session)

            for config in configs:
                if not config.bot_id:
                    continue

                # Получаем подписчиков
                chat_ids = await repo.get_subscribers_for_strategy(config.id)
                if not chat_ids:
                    continue

                # Формируем сообщение
                msg = self._format_message(event)

                # Рассылка
                for chat_id in chat_ids:
                    await self.bot_manager.send_message(config.bot_id, chat_id, msg)

    def _format_message(self, event: SignalEvent) -> str:
        emoji = "🟢" if event.direction == "BUY" else "🔴"
        return (
            f"{emoji} **SIGNAL: {event.direction}**\n"
            f"#{event.instrument}\n"
            f"Strategy: `{event.strategy_id}`\n"
            f"Time: `{event.timestamp.strftime('%H:%M:%S')}`"
        )