"""
Мост для публикации сигналов в Telegram (Telegram Publisher).

Этот модуль слушает внутреннюю шину событий (`SignalBus`) и при появлении
нового торгового сигнала (`SignalEvent`) организует его рассылку подписчикам
через соответствующие Telegram-боты.

Роль в архитектуре:
    Адаптер вывода (Driving Adapter). Он преобразует внутренние события предметной области
    во внешние уведомления.
"""

import asyncio
import logging
from typing import Optional

from sqlalchemy import select

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
    """
    Слушатель шины событий, отвечающий за доставку сигналов в Telegram.

    Класс решает задачу маршрутизации: по данным сигнала (тикер, стратегия)
    он находит в базе данных соответствующие конфигурации, определяет привязанных
    к ним ботов и их подписчиков, а затем инициирует рассылку.

    Attributes:
        bus (SignalBus): Шина событий для подписки.
        bot_manager (BotManager): Менеджер ботов для отправки сообщений.
        queue (Optional[asyncio.Queue]): Очередь событий.
    """

    def __init__(self, bus: SignalBus, bot_manager: BotManager):
        """
        Инициализирует мост.

        Args:
            bus (SignalBus): Глобальная шина событий.
            bot_manager (BotManager): Запущенный менеджер ботов.
        """
        self.bus = bus
        self.bot_manager = bot_manager
        self.queue: Optional[asyncio.Queue] = None

    async def start(self):
        """
        Запускает бесконечный цикл обработки событий.

        Подписывается на шину и ожидает появления сигналов. При получении
        сигнала вызывает логику маршрутизации и отправки.
        """
        self.queue = self.bus.subscribe()
        logger.info("TelegramBridge: Слушатель сигналов запущен...")

        while True:
            try:
                event = await self.queue.get()

                # Фильтруем только торговые сигналы
                if isinstance(event, SignalEvent):
                    await self._process_signal(event)

                self.queue.task_done()

            except asyncio.CancelledError:
                logger.info("TelegramBridge: Остановка слушателя.")
                break
            except Exception as e:
                logger.error(f"TelegramBridge: Ошибка при обработке события: {e}", exc_info=True)

    async def _process_signal(self, event: SignalEvent):
        """
        Маршрутизирует сигнал получателям.

        Алгоритм:
        1. Ищет в БД все активные `StrategyConfig`, которые соответствуют
           имени стратегии и инструменту из сигнала.
        2. Для каждого найденного конфига определяет ID бота.
        3. Получает список подписчиков этого бота.
        4. Формирует сообщение и отправляет его через `bot_manager`.

        Args:
            event (SignalEvent): Событие сигнала.
        """
        # TODO: В будущем добавить config_id прямо в SignalEvent, чтобы избежать поиска.
        # Сейчас мы ищем стратегию по косвенным признакам (имя + инструмент).

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
                # Сигнал есть, а активной стратегии в БД нет (странно, но возможно при рассинхроне)
                return

            repo = BotRepository(session)

            for config in configs:
                # Если стратегия не привязана к боту, уведомлять некого
                if not config.bot_id:
                    continue

                # Проверяем, запущен ли бот в менеджере прямо сейчас
                if config.bot_id not in self.bot_manager.active_bots:
                    logger.warning(
                        f"Сигнал получен, но бот ID {config.bot_id} отключен или не запущен. Пропуск рассылки.")
                    continue

                # Получаем список подписчиков этой стратегии (через бота)
                chat_ids = await repo.get_subscribers_for_strategy(config.id)
                if not chat_ids:
                    continue

                # Формируем текст
                msg = self._format_message(event)

                # Рассылка
                logger.info(f"Отправка сигнала стратегии '{config.strategy_name}' "
                            f"через бота {config.bot_id} для {len(chat_ids)} подписчиков.")

                for chat_id in chat_ids:
                    await self.bot_manager.send_message(config.bot_id, chat_id, msg)

    def _format_message(self, event: SignalEvent) -> str:
        """
        Форматирует сообщение сигнала для Telegram (Markdown).

        Добавляет эмодзи, переводит время закрытия свечи в МСК и
        формирует красивую структуру текста.

        Args:
            event (SignalEvent): Данные сигнала.

        Returns:
            str: Текст сообщения.
        """
        # 1. Рассчитываем время закрытия свечи (timestamp сигнала указывает на начало свечи + интервал)
        duration = parse_interval_to_timedelta(event.interval or "1min")
        close_time_utc = event.timestamp + duration

        # 2. Переводим в Московское время
        msk_time = close_time_utc.astimezone(msk_timezone())
        time_str = msk_time.strftime('%H:%M:%S')

        # 3. Заголовки и иконки
        if event.direction == TradeDirection.BUY:
            header = "🟢 **СИГНАЛ НА ПОКУПКУ (BUY)**"
        else:
            header = "🔴 **СИГНАЛ НА ПРОДАЖУ (SELL)**"

        price_str = f"`{event.price}`" if event.price else "_по рынку_"

        # 4. Сборка сообщения
        return (
            f"{header}\n\n"
            f"💎 **Инструмент:** `#{event.instrument}`\n"
            f"⏳ **Таймфрейм:** `{event.interval}`\n"
            f"⚡ **Направление:** `{event.direction}`\n"
            f"💵 **Цена (Close):** {price_str}\n"
            f"🧠 **Стратегия:** `{event.strategy_id}`\n"
            f"🕒 **Свеча закрыта:** `{time_str} (МСК)`\n\n"
            f"ℹ️ _Инфо: Цена сигнала соответствует цене закрытия свечи._\n\n"
            f"⚠️ _Дисклеймер:_\n"
            f"_Сигнал сформирован автоматически. Не является индивидуальной инвестиционной рекомендацией. "
            f"Принимайте торговые решения самостоятельно._"
        )