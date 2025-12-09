"""
Мост для публикации сигналов в Telegram (Telegram Publisher).

Принимает сигналы, находит подписчиков для конкретной стратегии
и рассылает уведомления через BotManager.
"""

import logging
from sqlalchemy import select

from app.shared.interfaces import SignalHandler
from app.adapters.telegram.manager import BotManager
from app.shared.events import SignalEvent
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import BotRepository
from app.infrastructure.database.models import StrategyConfig
from app.shared.time_helper import interval_to_timedelta, get_display_timezone
from app.shared.primitives import TradeDirection

logger = logging.getLogger(__name__)


class TelegramSignalSender(SignalHandler):
    """
    Сервис, отвечающий за доставку сигналов в Telegram.

    Attributes:
        bot_manager (BotManager): Менеджер ботов для физической отправки сообщений.
    """

    def __init__(self, bot_manager: BotManager):
        """
        Инициализирует сервис отправки.

        Args:
            bot_manager (BotManager): Активный менеджер ботов.
        """
        self.bot_manager = bot_manager

    async def handle_signal(self, event: SignalEvent) -> None:
        """
        Маршрутизирует сигнал соответствующим подписчикам.

        Алгоритм:
        1. Находит активные конфигурации стратегий в БД по имени стратегии и инструменту.
        2. Определяет ID бота, привязанного к конфигурации.
        3. Получает список подписчиков этого бота/стратегии.
        4. Отправляет сообщение.

        Args:
            event (SignalEvent): Входящий сигнал.
        """
        # TODO: В будущем добавить config_id прямо в SignalEvent, чтобы избежать поиска.
        async with async_session_factory() as session:
            # 1. Находим, каким конфигам принадлежит этот сигнал
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
                # Если стратегия не привязана к боту, уведомлять некого
                if not config.bot_id:
                    continue

                # Проверяем, запущен ли бот прямо сейчас
                if config.bot_id not in self.bot_manager.active_bots:
                    logger.warning(
                        f"Сигнал получен, но бот ID {config.bot_id} отключен или не запущен. Пропуск рассылки.")
                    continue

                # Получаем список подписчиков
                chat_ids = await repo.get_subscribers_for_strategy(config.id)
                if not chat_ids:
                    continue

                # Формируем текст сообщения
                msg = self._format_message(event)

                logger.info(f"Отправка сигнала стратегии '{config.strategy_name}' "
                            f"через бота {config.bot_id} для {len(chat_ids)} подписчиков.")

                for chat_id in chat_ids:
                    await self.bot_manager.send_message(config.bot_id, chat_id, msg)

    def _format_message(self, event: SignalEvent) -> str:
        """
        Форматирует сообщение сигнала для Telegram (Markdown).

        Args:
            event (SignalEvent): Данные сигнала.

        Returns:
            str: Текст сообщения.
        """
        duration = interval_to_timedelta(event.interval)
        close_time_utc = event.timestamp + duration

        # Используем глобальную настройку таймзоны
        local_time = close_time_utc.astimezone(get_display_timezone())
        time_str = local_time.strftime('%H:%M:%S')

        if event.direction == TradeDirection.BUY:
            header = "🟢 **СИГНАЛ НА ПОКУПКУ (BUY)**"
        else:
            header = "🔴 **СИГНАЛ НА ПРОДАЖУ (SELL)**"

        price_str = f"`{event.price:.4f}`" if event.price else "_по рынку_"

        return (
            f"{header}\n\n"
            f"💎 **Инструмент:** `#{event.instrument}`\n"
            f"⏳ **Таймфрейм:** `{event.interval}`\n"
            f"⚡ **Направление:** `{event.direction}`\n"
            f"💵 **Цена (Close):** {price_str}\n"
            f"🧠 **Стратегия:** `{event.strategy_id}`\n"
            f"🕒 **Свеча закрыта:** `{time_str} ({get_display_timezone().key})`\n\n"
            #my_question Вот почему close, а не open новой нельзя использовать? Или потому что close это как open новой и так даже лучше?
            f"ℹ️ _Цена сигнала соответствует цене закрытия свечи._\n" 
            f"⚠️ _Дисклеймер:_\n"
            f"_Не является индивидуальной инвестиционной рекомендацией. "
            f"Принимайте торговые решения самостоятельно._"
        )