import logging
import asyncio
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.storage.repositories import BotRepository
from app.storage.models import BotInstance

logger = logging.getLogger(__name__)


class BotManager:
    """
    Управляет жизненным циклом N телеграм-ботов.
    Запускает поллинг, регистрирует хендлеры.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory
        self.active_bots: Dict[int, Bot] = {}  # bot_db_id -> Bot object
        self.dp = Dispatcher()  # Один диспетчер на всех ботов

        # --- РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ---
        self.dp.message.register(self.cmd_start, Command("start"))

    async def cmd_start(self, message: types.Message, bot: Bot):
        """Обработка команды /start."""
        # Нам нужно понять, какому именно боту из БД написали.
        # aiogram передает объект bot в хендлер.
        # Мы найдем его ID в нашем словаре active_bots (обратный поиск).

        bot_db_id = None
        for bid, b_obj in self.active_bots.items():
            if b_obj.id == bot.id:  # aiogram bot id matches
                bot_db_id = bid
                break

        if bot_db_id is None:
            await message.answer("Ошибка конфигурации бота.")
            return

        async with self.session_factory() as session:
            repo = BotRepository(session)
            is_new = await repo.register_subscriber(
                bot_id=bot_db_id,
                chat_id=message.chat.id,
                username=message.from_user.username
            )

        if is_new:
            await message.answer("✅ Вы успешно подписались на сигналы этого бота!")
            logger.info(f"New subscriber {message.chat.id} for bot {bot_db_id}")
        else:
            await message.answer("Вы уже подписаны. Ожидайте сигналов.")

    async def start(self):
        """Загружает ботов из БД и запускает поллинг."""
        async with self.session_factory() as session:
            repo = BotRepository(session)
            bots_data = await repo.get_all_active_bots()

        if not bots_data:
            logger.warning("Нет активных ботов в БД для запуска.")
            return

        runners = []
        for bot_data in bots_data:
            try:
                bot = Bot(token=bot_data.token)
                # Сохраняем ссылку
                self.active_bots[bot_data.id] = bot

                # Удаляем вебхук на всякий случай и запускаем
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info(f"Bot started: {bot_data.name}")

                # Создаем polling task для каждого бота
                # Используем polling диспетчера, передавая список ботов?
                # В aiogram 3.x мульти-бот делается немного иначе, но
                # самый простой способ - запустить dp.start_polling для списка ботов,
                # но пока сделаем для одного, или через цикл.
                pass
            except Exception as e:
                logger.error(f"Failed to start bot {bot_data.name}: {e}")

        if self.active_bots:
            # Запускаем поллинг для всех созданных ботов
            bots_list = list(self.active_bots.values())
            await self.dp.start_polling(*bots_list)

    async def send_message(self, bot_id: int, chat_id: int, text: str):
        """Отправка сообщения конкретному пользователю от конкретного бота."""
        bot = self.active_bots.get(bot_id)
        if bot:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send msg to {chat_id}: {e}")