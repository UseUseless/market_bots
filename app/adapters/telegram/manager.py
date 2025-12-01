"""
Менеджер Telegram-ботов (Multi-Bot Manager).

Этот модуль отвечает за управление жизненным циклом (запуск, остановка, перезагрузка)
нескольких Telegram-ботов в одном приложении.

Ключевые особенности:
1. **Dynamic Polling:** Боты запускаются и останавливаются на лету на основе записей в БД.
2. **Centralized Dispatcher:** Используется один диспетчер `aiogram` для всех ботов,
   что упрощает регистрацию хендлеров команд (например, /start).
3. **Broadcasting:** Механизм рассылки сообщений подписчикам конкретного бота.
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

    Периодически опрашивает базу данных на предмет изменений в списке активных ботов.
    Если бот добавлен или активирован — запускает для него задачу Polling.
    Если бот удален или деактивирован — корректно останавливает его задачу.

    Attributes:
        session_factory (async_sessionmaker): Фабрика для создания сессий БД.
        active_bots (Dict[int, Bot]): Реестр запущенных объектов Bot {db_id: Bot}.
        polling_tasks (Dict[int, asyncio.Task]): Реестр фоновых задач поллинга {db_id: Task}.
        dp (Dispatcher): Глобальный диспетчер aiogram.
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

        # Создаем диспетчер. Он будет обрабатывать апдейты от ВСЕХ ботов.
        self.dp = Dispatcher()

        # Регистрация глобальных обработчиков команд
        self.dp.message.register(self.cmd_start, Command("start"))

    async def cmd_start(self, message: types.Message, bot: Bot):
        """
        Обработчик команды /start.

        Регистрирует пользователя в базе данных как подписчика того бота,
        которому он написал.

        Args:
            message (types.Message): Сообщение от пользователя.
            bot (Bot): Экземпляр бота, принявшего сообщение (внедряется aiogram).
        """
        # 1. Определяем ID бота в нашей базе данных
        # aiogram.Bot.id - это ID от Telegram, нам нужно сопоставить его с нашим PK
        bot_db_id = None
        for bid, b_obj in self.active_bots.items():
            if b_obj.id == bot.id:
                bot_db_id = bid
                break

        if bot_db_id is None:
            await message.answer("⚠️ Ошибка: Конфигурация бота не найдена.")
            return

        # 2. Регистрируем подписчика в транзакции
        async with self.session_factory() as session:
            repo = BotRepository(session)

            # Проверка безопасности: активен ли бот в БД?
            # (теоретически бот может быть выключен в БД, но таск еще работает пару секунд)
            bots_data = await repo.get_all_active_bots()
            if bot_db_id not in [b.id for b in bots_data]:
                await message.answer("⚠️ Этот бот временно отключен.")
                return

            is_new = await repo.register_subscriber(
                bot_id=bot_db_id,
                chat_id=message.chat.id,
                username=message.from_user.username
            )

        # 3. Отправляем ответ
        if is_new:
            await message.answer("✅ Вы успешно подписались на сигналы!")
            logger.info(f"Новый подписчик {message.chat.id} у бота ID {bot_db_id}")
        else:
            await message.answer("Вы уже подписаны. Ожидайте новых сигналов.")

    async def _start_bot_polling(self, bot_id: int, bot: Bot):
        """
        Запускает Long Polling для одного конкретного бота.

        Эта функция работает как бесконечный цикл внутри asyncio.Task.

        Args:
            bot_id (int): ID бота в БД (для логирования).
            bot (Bot): Объект aiogram.Bot.
        """
        try:
            # Очистка вебхука обязательна перед поллингом, иначе Telegram вернет ошибку
            await bot.delete_webhook(drop_pending_updates=True)

            # Запуск поллинга. Метод polling() блокирующий, поэтому он запускается в Task.
            await self.dp.start_polling(bot)
        except asyncio.CancelledError:
            logger.info(f"Остановка поллинга для бота ID {bot_id}")
            raise
        except Exception as e:
            logger.error(f"Критическая ошибка поллинга бота ID {bot_id}: {e}")

    async def _broadcast(self, bot_id: int, text: str):
        """
        Служебная рассылка сообщения всем подписчикам конкретного бота.
        Используется для уведомлений о статусе (запуск/остановка).

        Args:
            bot_id (int): ID бота.
            text (str): Текст сообщения (Markdown поддерживается).
        """
        async with self.session_factory() as session:
            repo = BotRepository(session)
            chat_ids = await repo.get_all_subscribers_for_bot(bot_id)

        if not chat_ids:
            return

        logger.info(f"Рассылка для {len(chat_ids)} подписчиков через бота ID {bot_id}")
        bot = self.active_bots.get(bot_id)
        if not bot:
            return

        # Простая последовательная рассылка (для >1000 пользователей стоит использовать очередь)
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
                # Лимит Telegram: ~30 сообщений в секунду. Делаем паузу.
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.warning(f"Не удалось отправить сообщение {chat_id}: {e}")

    async def start(self):
        """
        Главный цикл Watchdog.

        Бесконечно проверяет базу данных на наличие изменений в списке активных ботов.
        Синхронизирует состояние `self.active_bots` с состоянием БД.
        """
        logger.info("🤖 Bot Manager Orchestrator started.")
        try:
            while True:
                try:
                    # 1. Получаем актуальное состояние из БД
                    async with self.session_factory() as session:
                        repo = BotRepository(session)
                        db_bots = await repo.get_all_active_bots()

                    current_ids = set(self.active_bots.keys())
                    target_ids = {b.id for b in db_bots}
                    db_bots_map = {b.id: b for b in db_bots}

                    # 2. Вычисляем дельту
                    ids_to_add = target_ids - current_ids
                    ids_to_remove = current_ids - target_ids

                    # --- ОСТАНОВКА БОТОВ (которые выключили в БД) ---
                    for bid in ids_to_remove:
                        logger.info(f"🛑 Остановка бота ID {bid}...")

                        # Уведомляем пользователей
                        await self._broadcast(bid,
                                              "💤 **Бот приостанавливает работу.**\n"
                                              "Мониторинг временно отключен администратором.")

                        # Отменяем задачу поллинга
                        if bid in self.polling_tasks:
                            self.polling_tasks[bid].cancel()
                            try:
                                await self.polling_tasks[bid]
                            except asyncio.CancelledError:
                                pass
                            del self.polling_tasks[bid]

                        # Закрываем сессию aiohttp
                        bot = self.active_bots.pop(bid)
                        await bot.session.close()
                        logger.info(f"Бот ID {bid} остановлен.")

                    # --- ЗАПУСК БОТОВ (которые включили в БД) ---
                    for bid in ids_to_add:
                        bot_data = db_bots_map[bid]
                        try:
                            logger.info(f"🆕 Запуск бота ID {bid}: {bot_data.name}")
                            bot = Bot(token=bot_data.token)

                            # Проверка токена через getMe
                            bot_user = await bot.get_me()
                            logger.info(f"   Авторизован как @{bot_user.username}")

                            self.active_bots[bid] = bot

                            # Создаем фоновую задачу для поллинга
                            task = asyncio.create_task(self._start_bot_polling(bid, bot))
                            self.polling_tasks[bid] = task

                            await self._broadcast(bid,
                                                  "🚀 **Бот активирован!**\n"
                                                  "Система мониторинга запущена. Ожидайте сигналов.")

                        except Exception as e:
                            logger.error(f"❌ Не удалось запустить бота {bot_data.name}: {e}")

                except asyncio.CancelledError:
                    raise  # Пробрасываем наверх для корректного выхода

                except Exception as e:
                    logger.error(f"Bot Manager Loop Error: {e}")

                # Пауза перед следующей проверкой БД
                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info("BotManager: Получен сигнал остановки.")
        finally:
            logger.info("BotManager: Закрытие сессий всех ботов...")
            for bid, bot in self.active_bots.items():
                await bot.session.close()
            self.active_bots.clear()
            logger.info("BotManager: Работа завершена.")

    async def send_message(self, bot_id: int, chat_id: int, text: str):
        """
        Публичный метод для отправки сообщения конкретному пользователю от конкретного бота.
        Используется модулем `publisher` для рассылки сигналов.

        Args:
            bot_id (int): ID бота-отправителя.
            chat_id (int): ID чата получателя.
            text (str): Текст сообщения.
        """
        bot = self.active_bots.get(bot_id)
        if bot:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения в {chat_id} через бота {bot_id}: {e}")
        else:
            # Если бота нет в активных, значит он выключен в БД, но сигнал почему-то пришел
            logger.warning(f"Попытка отправки через неактивного бота ID {bot_id}")