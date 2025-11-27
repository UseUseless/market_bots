import asyncio
import logging

from app.core.event_bus import SignalBus
from app.shared.events import SignalEvent
from app.adapters.telegram.manager import BotManager
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import BotRepository
from app.infrastructure.database.models import StrategyConfig
from app.shared.time_helper import parse_interval_to_timedelta, msk_timezone
from app.shared.primitives import TradeDirection

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

                # Проверяем, жив ли бот в менеджере прямо сейчас
                if config.bot_id not in self.bot_manager.active_bots:
                    logger.warning(
                        f"Signal generated, but Bot ID {config.bot_id} is disabled/offline. Skipping broadcast.")
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
        # 1. Считаем время
        duration = parse_interval_to_timedelta(event.interval)
        close_time_utc = event.timestamp + duration

        # 2. Переводим в МСК
        msk_time = close_time_utc.astimezone(msk_timezone())
        time_str = msk_time.strftime('%H:%M:%S')

        if event.direction == TradeDirection.BUY:
            header = "🟢 **СИГНАЛ НА ПОКУПКУ (BUY)**"
        else:
            header = "🔴 **СИГНАЛ НА ПРОДАЖУ (SELL)**"

        price_str = f"`{event.price}`" if event.price else "_по рынку_"

        return (
            f"{header}\n\n"
            f"💎 **Инструмент:** `#{event.instrument}`\n"
            f"⏳ **Таймфрейм:** `{event.interval}`\n"
            f"⚡ **Направление:** `{event.direction}`\n"
            f"💵 **Цена (Close):** {price_str}\n"
            f"🧠 **Стратегия:** `{event.strategy_id}`\n"
            f"🕒 **Свеча закрыта:** `{time_str} (МСК)`\n\n"
            f"ℹ️ _Инфо: Цена сигнала соответствует цене закрытия свечи. Время указано на момент окончания формирования свечи._\n\n"
            f"⚠️ _Дисклеймер:_\n"
            f"_Сигнал сформирован автоматически. Не является инвест-рекомендацией. "
            f"Принимайте решения самостоятельно._"
        )