"""
Модуль публикации сигналов в Telegram (Notification Adapter).

Этот модуль реализует интерфейс `SignalHandler` и служит мостом между
внутренней шиной событий (Event Bus) и внешним миром (Telegram).
Он отвечает за:
1. Фильтрацию сигналов (кому отправлять?).
2. Форматирование сообщений (Markdown).
3. Маршрутизацию сообщений через `BotManager`.
"""

import logging
from sqlalchemy import select

from app.shared.interfaces import SignalHandler
from app.adapters.telegram.manager import BotManager
from app.shared.events import SignalEvent
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import BotRepository
from app.infrastructure.database.models import StrategyConfig
from app.shared.types import TradeDirection
from app.shared.time_utils import get_display_timezone

logger = logging.getLogger(__name__)


class TelegramSignalSender(SignalHandler):
    """
    Обработчик сигналов для отправки уведомлений в Telegram.

    При получении сигнала этот класс находит в базе данных все активные
    конфигурации стратегий, соответствующие имени стратегии и инструменту
    сигнала, и рассылает уведомления подписчикам соответствующих ботов.

    Attributes:
        bot_manager (BotManager): Менеджер ботов для физической отправки сообщений.
    """

    def __init__(self, bot_manager: BotManager):
        """
        Инициализирует отправителя сигналов.

        Args:
            bot_manager (BotManager): Экземпляр менеджера, управляющий ботами.
        """
        self.bot_manager = bot_manager

    async def handle_signal(self, event: SignalEvent) -> None:
        """
        Обрабатывает входящее событие сигнала.

        Алгоритм работы:
        1. Открывает сессию БД.
        2. Ищет активные конфигурации (`StrategyConfig`), соответствующие
           параметрам сигнала (имя стратегии, тикер).
        3. Для каждой найденной конфигурации находит подписчиков привязанного бота.
        4. Форматирует сообщение и отправляет его каждому подписчику.

        Args:
            event (SignalEvent): Событие торгового сигнала.
        """
        async with async_session_factory() as session:
            # 1. Поиск конфигураций в БД
            # Нам нужно найти, какие именно настройки (пары/таймфреймы)
            # сгенерировали этот сигнал.
            query = select(StrategyConfig).where(
                StrategyConfig.strategy_name == event.strategy_name,
                StrategyConfig.instrument == event.instrument,
                StrategyConfig.is_active == True
            )
            result = await session.execute(query)
            configs = result.scalars().all()

            if not configs:
                # Сигнал есть, но в БД нет активной стратегии с таким именем/тикером.
                # Это может случиться, если стратегию отключили в UI, а процесс еще работал.
                return

            repo = BotRepository(session)

            # 2. Рассылка по найденным конфигурациям
            for config in configs:
                # Пропускаем, если к стратегии не привязан бот
                if not config.bot_id:
                    continue

                # Пропускаем, если сам бот не запущен в менеджере (например, неверный токен)
                if config.bot_id not in self.bot_manager.active_bots:
                    continue

                # Получаем список ID чатов подписчиков
                chat_ids = await repo.get_subscribers_for_strategy(config.id)
                if not chat_ids:
                    continue

                # Формируем текст сообщения
                msg = self._format_message(event, config)

                # Отправляем каждому подписчику
                for chat_id in chat_ids:
                    await self.bot_manager.send_message(config.bot_id, chat_id, msg)

    def _format_message(self, event: SignalEvent, config: StrategyConfig) -> str:
        """
        Формирует текст сообщения для Telegram в формате Markdown.

        Args:
            event (SignalEvent): Данные сигнала.
            config (StrategyConfig): Конфигурация стратегии (для получения таймфрейма).

        Returns:
            str: Отформатированная строка сообщения.
        """
        # Конвертация времени в часовой пояс пользователя (из настроек)
        local_time = event.timestamp.astimezone(get_display_timezone())
        time_str = local_time.strftime('%H:%M:%S')

        # Заголовок с направлением (Зеленый/Красный)
        header = "🟢 **BUY**" if event.direction == TradeDirection.BUY else "🔴 **SELL**"

        return (
            f"{header} | #{event.instrument}\n"
            f"🧠 {event.strategy_name} ({config.interval})\n"
            f"💵 {event.price:.4f}\n"
            f"🕒 {time_str}\n"
            f"ℹ️ _Цена сигнала соответствует цене закрытия свечи._\n"
            f"⚠️ _Дисклеймер:_\n"
            f"_Не является индивидуальной инвестиционной рекомендацией. "
            f"Принимайте торговые решения самостоятельно._"
        )