"""
Менеджер Telegram-ботов (Multi-Bot Manager).

Этот модуль отвечает за управление жизненным циклом (запуск, остановка, перезагрузка)
нескольких Telegram-ботов в одном приложении.

Архитектура:
    Используется паттерн изолированных диспетчеров. Для каждого бота создается
    свой собственный `Dispatcher`.

1.  Гарантированное удаление вебхука перед запуском.
2.  Задержка (Cool-down) после остановки для предотвращения TelegramConflictError.
3.  Защита от сбоев подключения к БД (Retry logic).
4.  Безопасное закрытие сессий aiohttp.
"""

import logging
import asyncio
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infrastructure.database.repositories import BotRepository

logger = logging.getLogger(__name__)


class BotManager:
    """
    Оркестратор Telegram-ботов.

    Управляет массивом объектов `Bot` и соответствующими им асинхронными задачами (Tasks).
    """

    def __init__(self, session_factory: async_sessionmaker):
        """
        Инициализирует менеджер.

        Args:
            session_factory: Асинхронная фабрика сессий SQLAlchemy.
        """
        self.session_factory = session_factory
        self.active_bots: Dict[int, Bot] = {}
        self.polling_tasks: Dict[int, asyncio.Task] = {}
        self.dispatchers: Dict[int, Dispatcher] = {}

    async def cmd_start(self, message: types.Message, bot: Bot):
        """
        Обработчик команды /start.
        """
        bot_db_id = None
        for bid, b_obj in self.active_bots.items():
            if b_obj.id == bot.id:
                bot_db_id = bid
                break

        if bot_db_id is None:
            await message.answer("⚠️ Ошибка: Бот не найден в конфигурации.")
            return

        # Используем defensive pattern для работы с БД внутри хендлера
        try:
            async with self.session_factory() as session:
                repo = BotRepository(session)
                bots_data = await repo.get_all_active_bots()
                if bot_db_id not in [b.id for b in bots_data]:
                    return

                is_new = await repo.register_subscriber(
                    bot_id=bot_db_id,
                    chat_id=message.chat.id,
                    username=message.from_user.username
                )

            if is_new:
                await message.answer("✅ Вы успешно подписались на сигналы!")
                logger.info(f"Новый подписчик {message.chat.id} у бота ID {bot_db_id}")
            else:
                await message.answer("Вы уже подписаны.")
        except Exception as e:
            logger.error(f"Ошибка БД в cmd_start: {e}")
            await message.answer("⚠️ Внутренняя ошибка сервера. Попробуйте позже.")

    async def _run_isolated_polling(self, bot_id: int, bot: Bot):
        """
        Воркер для запуска polling в изолированном диспетчере.
        """
        dp = Dispatcher()
        dp.message.register(self.cmd_start, Command("start"))
        self.dispatchers[bot_id] = dp

        try:
            logger.info(f"BotManager: Starting polling for bot {bot_id}")
            await dp.start_polling(bot, handle_signals=False)

        except asyncio.CancelledError:
            logger.info(f"BotManager: Polling cancelled for bot {bot_id}")
            raise

        except Exception as e:
            logger.error(f"BotManager: Error in bot {bot_id}: {e}", exc_info=True)

        finally:
            # Чистка ресурсов при выходе
            if bot_id in self.dispatchers:
                del self.dispatchers[bot_id]

            try:
                if hasattr(bot, 'session') and bot.session:
                    await bot.session.close()
                    logger.info(f"BotManager: Session closed for bot {bot_id}")
            except Exception as ex:
                logger.warning(f"BotManager: Error closing session for bot {bot_id}: {ex}")

    async def _broadcast(self, bot_id: int, text: str):
        """Рассылка системных уведомлений."""
        try:
            async with self.session_factory() as session:
                repo = BotRepository(session)
                chat_ids = await repo.get_all_subscribers_for_bot(bot_id)
        except Exception as e:
            logger.error(f"DB Error in broadcast: {e}")
            return

        bot = self.active_bots.get(bot_id)
        if not bot or not chat_ids:
            return

        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
                await asyncio.sleep(0.05)
            except Exception:
                pass

    async def start(self):
        """
        Главный цикл Watchdog (Оркестратор).
        """
        logger.info("🤖 Bot Manager Orchestrator started.")

        if not self.session_factory:
            logger.critical("BotManager: session_factory is None! Exiting.")
            return

        try:
            while True:
                db_bots = []
                try:
                    async with self.session_factory() as session:
                        repo = BotRepository(session)
                        db_bots = await repo.get_all_active_bots()
                except Exception as e:
                    logger.error(f"DB Error in BotManager loop: {e}. Retrying in 5s...")
                    await asyncio.sleep(5)
                    continue

                target_ids = {b.id for b in db_bots}
                current_ids = set(self.active_bots.keys())
                db_bots_map = {b.id: b for b in db_bots}

                # --- ОСТАНОВКА ---
                ids_to_remove = current_ids - target_ids
                for bid in ids_to_remove:
                    logger.info(f"🔻 Stopping bot ID {bid}...")

                    # Пытаемся отправить прощальное сообщение, но не блокируемся ошибкой
                    try:
                        await self._broadcast(bid, "💤 **Бот остановлен.**")
                    except Exception:
                        pass

                    task = self.polling_tasks.get(bid)
                    if task:
                        dp = self.dispatchers.get(bid)
                        if dp:
                            try:
                                await dp.stop_polling()
                            except Exception:
                                pass
                        
                        # Теперь отменяем задачу
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.error(f"Stop error for bot {bid}: {e}")

                        del self.polling_tasks[bid]

                    if bid in self.active_bots:
                        del self.active_bots[bid]

                    logger.info(f"✅ Bot ID {bid} stopped.")

                    await asyncio.sleep(3)

                # --- ЗАПУСК ---
                ids_to_add = target_ids - current_ids
                for bid in ids_to_add:
                    bot_data = db_bots_map[bid]
                    try:
                        logger.info(f"🆕 Starting bot ID {bid} ({bot_data.name})")
                        bot = Bot(token=bot_data.token)

                        logger.info(f"BotManager: Cleaning webhook before start for bot {bid}...")
                        await bot.delete_webhook(drop_pending_updates=True)
                        
                        self.active_bots[bid] = bot

                        task = asyncio.create_task(self._run_isolated_polling(bid, bot))
                        self.polling_tasks[bid] = task

                        await self._broadcast(bid, "🚀 **Бот запущен!**")

                    except Exception as e:
                        logger.error(f"Failed to start bot ID {bid}: {e}")
                        # Если не удалось запустить, убираем из активных, чтобы попробовать снова в след. итерации
                        if bid in self.active_bots:
                            del self.active_bots[bid]

                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info("BotManager: Shutting down...")
            for task in self.polling_tasks.values():
                task.cancel()
            if self.polling_tasks:
                await asyncio.gather(*self.polling_tasks.values(), return_exceptions=True)
            logger.info("BotManager: All bots stopped.")

    async def send_message(self, bot_id: int, chat_id: int, text: str):
        """
        Отправка сообщения конкретному пользователю.
        """
        bot = self.active_bots.get(bot_id)
        if bot:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Send error for bot {bot_id}: {e}")
        else:
            logger.warning(f"Bot ID {bot_id} not active.")