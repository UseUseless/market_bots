import logging
import asyncio
from typing import Dict

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.storage.repositories import BotRepository

logger = logging.getLogger(__name__)


class BotManager:
    """
    Управляет жизненным циклом N телеграм-ботов с поддержкой Hot Reload.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory
        self.active_bots: Dict[int, Bot] = {}
        self.polling_tasks: Dict[int, asyncio.Task] = {}
        self.dp = Dispatcher()

        # --- РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ---
        self.dp.message.register(self.cmd_start, Command("start"))

    async def cmd_start(self, message: types.Message, bot: Bot):
        """Обработка команды /start."""
        # Находим ID бота в нашей базе
        bot_db_id = None
        for bid, b_obj in self.active_bots.items():
            if b_obj.id == bot.id:
                bot_db_id = bid
                break

        if bot_db_id is None:
            await message.answer("⚠️ Ошибка: Бот не найден в конфигурации.")
            return

        async with self.session_factory() as session:
            repo = BotRepository(session)
            # Дополнительная проверка статуса при получении команды
            # (на случай, если бот выключается, но поллинг еще не остановился)
            bots_data = await repo.get_all_active_bots()
            if bot_db_id not in [b.id for b in bots_data]:
                await message.answer("⚠️ Этот бот отключен администратором.")
                return

            is_new = await repo.register_subscriber(
                bot_id=bot_db_id,
                chat_id=message.chat.id,
                username=message.from_user.username
            )

        if is_new:
            await message.answer("✅ Вы успешно подписались на сигналы!")
            logger.info(f"New subscriber {message.chat.id} for bot {bot_db_id}")
        else:
            await message.answer("Вы уже подписаны. Ожидайте сигналов.")

    async def _start_bot_polling(self, bot_id: int, bot: Bot):
        """Запускает поллинг для ОДНОГО конкретного бота."""
        try:
            # Удаляем вебхук, чтобы не конфликтовать с поллингом
            await bot.delete_webhook(drop_pending_updates=True)

            # Запускаем поллинг конкретного бота с общим диспетчером
            await self.dp.start_polling(bot)
        except asyncio.CancelledError:
            logger.info(f"Polling stopped for bot ID {bot_id}")
            raise
        except Exception as e:
            logger.error(f"Polling error for bot ID {bot_id}: {e}")

    async def _broadcast(self, bot_id: int, text: str):
        """Рассылает сообщение всем подписчикам бота."""
        async with self.session_factory() as session:
            repo = BotRepository(session)
            chat_ids = await repo.get_all_subscribers_for_bot(bot_id)

        if not chat_ids:
            return

        logger.info(f"Broadcasting to {len(chat_ids)} users via bot {bot_id}")
        bot = self.active_bots.get(bot_id)
        if not bot:
            return

        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
                # Небольшая задержка, чтобы не упереться в лимиты Телеграма
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.warning(f"Failed to broadcast to {chat_id}: {e}")

    async def start(self):
        """
        Главный цикл-менеджер (Watchdog).
        Следит за БД и управляет задачами поллинга.
        """
        logger.info("🤖 Bot Manager Orchestrator started.")

        while True:
            try:
                async with self.session_factory() as session:
                    repo = BotRepository(session)
                    # Получаем список ботов, у которых is_active = 1
                    db_bots = await repo.get_all_active_bots()

                current_ids = set(self.active_bots.keys())
                target_ids = {b.id for b in db_bots}
                db_bots_map = {b.id: b for b in db_bots}

                # 1. Находим новых ботов для запуска
                ids_to_add = target_ids - current_ids
                # 2. Находим выключенных ботов для остановки
                ids_to_remove = current_ids - target_ids

                # --- STOPPING ---
                for bid in ids_to_remove:
                    logger.info(f"🛑 Stopping bot ID {bid}...")

                    # 1. Прощаемся перед отключением
                    await self._broadcast(bid,
                                          "💤 **Бот приостанавливает работу.**\nМониторинг сигналов временно отключен.")

                    # Отменяем задачу поллинга
                    if bid in self.polling_tasks:
                        self.polling_tasks[bid].cancel()
                        try:
                            await self.polling_tasks[bid]
                        except asyncio.CancelledError:
                            pass
                        del self.polling_tasks[bid]

                    # Закрываем сессию бота
                    bot = self.active_bots.pop(bid)
                    await bot.session.close()
                    logger.info(f"Bot ID {bid} stopped.")

                # --- STARTING ---
                for bid in ids_to_add:
                    bot_data = db_bots_map[bid]
                    try:
                        logger.info(f"🆕 Starting bot ID {bid}: {bot_data.name}")
                        bot = Bot(token=bot_data.token)

                        # Проверка токена
                        bot_user = await bot.get_me()
                        logger.info(f"   Authorized as @{bot_user.username}")

                        self.active_bots[bid] = bot

                        # Запускаем поллинг в отдельной задаче
                        task = asyncio.create_task(self._start_bot_polling(bid, bot))
                        self.polling_tasks[bid] = task

                        await self._broadcast(bid,
                                              "🚀 **Бот активирован!**\nСистема мониторинга запущена. Ожидайте сигналов.")

                    except Exception as e:
                        logger.error(f"❌ Failed to start bot {bot_data.name}: {e}")

            except Exception as e:
                logger.error(f"Bot Manager loop error: {e}")

            # Пауза перед следующей проверкой БД
            await asyncio.sleep(5)

    async def send_message(self, bot_id: int, chat_id: int, text: str):
        """Отправка сообщения (если бот активен)."""
        bot = self.active_bots.get(bot_id)
        if bot:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Failed to send msg to {chat_id} via bot {bot_id}: {e}")
        else:
            # Если бота нет в active_bots, значит он выключен в БД
            logger.warning(f"Attempt to send message via disabled bot {bot_id}")