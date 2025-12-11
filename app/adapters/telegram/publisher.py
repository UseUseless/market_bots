"""
Мост для публикации сигналов в Telegram.
"""
import logging
from sqlalchemy import select

from app.shared.interfaces import SignalHandler
from app.adapters.telegram.manager import BotManager
from app.shared.events import SignalEvent
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import BotRepository
from app.infrastructure.database.models import StrategyConfig
from app.shared.primitives import TradeDirection
from app.shared.time_helper import get_display_timezone

logger = logging.getLogger(__name__)

class TelegramSignalSender(SignalHandler):
    def __init__(self, bot_manager: BotManager):
        self.bot_manager = bot_manager

    async def handle_signal(self, event: SignalEvent) -> None:
        async with async_session_factory() as session:
            # 1. Ищем конфиг по имени стратегии и инструменту
            # Это связывает абстрактный сигнал с конкретной настройкой в БД
            query = select(StrategyConfig).where(
                StrategyConfig.strategy_name == event.strategy_name,
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

                # Пропускаем, если бот не запущен
                if config.bot_id not in self.bot_manager.active_bots:
                    continue

                # Получаем подписчиков
                chat_ids = await repo.get_subscribers_for_strategy(config.id)
                if not chat_ids:
                    continue

                msg = self._format_message(event, config)

                for chat_id in chat_ids:
                    await self.bot_manager.send_message(config.bot_id, chat_id, msg)

    def _format_message(self, event: SignalEvent, config: StrategyConfig) -> str:
        local_time = event.timestamp.astimezone(get_display_timezone())
        time_str = local_time.strftime('%H:%M:%S')

        header = "🟢 **BUY**" if event.direction == TradeDirection.BUY else "🔴 **SELL**"

        return (
            f"{header} | #{event.instrument}\n"
            f"🧠 {event.strategy_name} ({config.interval})\n"
            f"💵 {event.price:.4f}\n"
            f"🕒 {time_str}"
            # my_question Вот почему close, а не open новой нельзя использовать? Или потому что close это как open новой и так даже лучше?
            f"ℹ️ _Цена сигнала соответствует цене закрытия свечи._\n"
            f"⚠️ _Дисклеймер:_\n"
            f"_Не является индивидуальной инвестиционной рекомендацией. "
            f"Принимайте торговые решения самостоятельно._"
        )