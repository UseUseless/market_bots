"""
Менеджер Telegram-ботов (Multi-Bot Manager).

Этот модуль отвечает за управление жизненным циклом (запуск, остановка, перезагрузка)
нескольких Telegram-ботов в рамках одного приложения. Реализует паттерн Watchdog,
синхронизируя запущенные процессы с конфигурацией в базе данных.
"""

import logging
import asyncio
from typing import Dict, Optional, Set

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.infrastructure.database.repositories import BotRepository

logger = logging.getLogger(__name__)


class BotManager:
    """
    Оркестратор для управления множеством Telegram-ботов.

    Создает изолированные диспетчеры для каждого токена, управляет задачами поллинга
    и предоставляет интерфейс для отправки сообщений из других частей системы.

    Attributes:
        session_factory (async_sessionmaker): Фабрика сессий БД для обработчиков команд.
        active_bots (Dict[int, Bot]): Реестр запущенных экземпляров ботов {bot_id: Bot}.
        polling_tasks (Dict[int, asyncio.Task]): Реестр фоновых задач поллинга.
        dispatchers (Dict[int, Dispatcher]): Реестр диспетчеров (один на каждого бота).
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
        self._running = False

    async def cmd_start(self, message: types.Message, bot: Bot):
        """
        Обработчик команды /start.

        Регистрирует пользователя в базе данных как подписчика конкретного бота.
        Использует переданный экземпляр `bot` для идентификации, к какому именно
        боту обратился пользователь.

        Args:
            message: Объект сообщения от пользователя.
            bot: Экземпляр бота, получивший сообщение.
        """
        # Поиск ID бота в нашей БД по объекту aiogram.Bot
        bot_db_id = None
        for bid, b_obj in self.active_bots.items():
            if b_obj.id == bot.id:
                bot_db_id = bid
                break

        if bot_db_id is None:
            await message.answer("⚠️ Ошибка конфигурации: Бот не найден в системе.")
            return

        # Работа с БД: регистрация подписчика
        try:
            async with self.session_factory() as session:
                repo = BotRepository(session)

                # Проверка, активен ли бот в БД (на случай рассинхрона)
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
                await message.answer("Вы уже подписаны на уведомления.")

        except Exception as e:
            logger.error(f"Ошибка БД в cmd_start: {e}", exc_info=True)
            await message.answer("⚠️ Внутренняя ошибка сервера. Попробуйте позже.")

    async def _run_isolated_polling(self, bot_id: int, bot: Bot):
        """
        Запускает Long Polling для конкретного бота в изолированном контексте.

        Создает отдельный Dispatcher, регистрирует хендлеры и запускает цикл.

        Args:
            bot_id: ID бота в базе данных.
            bot: Инициализированный объект aiogram.Bot.
        """
        dp = Dispatcher()

        # Регистрация хендлеров. Важно: передаем метод инстанса, чтобы был доступ к self.
        dp.message.register(self.cmd_start, Command("start"))

        self.dispatchers[bot_id] = dp

        try:
            logger.info(f"BotManager: Запуск поллинга для бота ID {bot_id}")
            # handle_signals=False, так как мы управляем сигналами остановки (SIGINT) глобально
            await dp.start_polling(bot, handle_signals=False)

        except asyncio.CancelledError:
            logger.info(f"BotManager: Поллинг отменен для бота ID {bot_id}")
            raise

        except Exception as e:
            logger.error(f"BotManager: Критическая ошибка в боте ID {bot_id}: {e}", exc_info=True)

        finally:
            # Очистка ресурсов при остановке задачи
            if bot_id in self.dispatchers:
                del self.dispatchers[bot_id]

            try:
                if hasattr(bot, 'session') and bot.session:
                    await bot.session.close()
                    logger.info(f"BotManager: Сессия закрыта для бота ID {bot_id}")
            except Exception as ex:
                logger.warning(f"BotManager: Ошибка закрытия сессии бота {bot_id}: {ex}")

    async def _broadcast_system_message(self, bot_id: int, text: str):
        """
        Рассылает системное уведомление всем подписчикам бота.

        Args:
            bot_id: ID бота.
            text: Текст сообщения (Markdown).
        """
        try:
            async with self.session_factory() as session:
                repo = BotRepository(session)
                chat_ids = await repo.get_all_subscribers_for_bot(bot_id)
        except Exception as e:
            logger.error(f"DB Error during broadcast: {e}")
            return

        bot = self.active_bots.get(bot_id)
        if not bot or not chat_ids:
            return

        # Массовая рассылка с небольшой задержкой во избежание флуд-лимитов
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
                await asyncio.sleep(0.05)
            except Exception:
                # Игнорируем ошибки доставки (блок бота пользователем и т.д.)
                pass

    async def start(self):
        """
        Главный цикл Watchdog (Оркестратор).

        Периодически опрашивает базу данных, выявляет изменения в списке активных ботов
        и синхронизирует состояние (запускает новых, останавливает удаленных).
        """
        logger.info("🤖 Bot Manager Orchestrator started.")
        self._running = True

        if not self.session_factory:
            logger.critical("BotManager: session_factory не установлен! Выход.")
            return

        try:
            while self._running:
                # 1. Получение актуального списка ботов из БД
                db_bots = []
                try:
                    async with self.session_factory() as session:
                        repo = BotRepository(session)
                        db_bots = await repo.get_all_active_bots()
                except Exception as e:
                    logger.error(f"Ошибка подключения к БД в BotManager: {e}. Рестарт через 5с...")
                    await asyncio.sleep(5)
                    continue

                target_ids = {b.id for b in db_bots}
                current_ids = set(self.active_bots.keys())
                db_bots_map = {b.id: b for b in db_bots}

                # 2. Остановка ботов, которых больше нет в конфиге
                ids_to_remove = current_ids - target_ids
                for bid in ids_to_remove:
                    await self._stop_bot_process(bid)

                # 3. Запуск новых ботов
                ids_to_add = target_ids - current_ids
                for bid in ids_to_add:
                    bot_data = db_bots_map[bid]
                    await self._start_bot_process(bid, bot_data)

                # Пауза перед следующей проверкой
                await asyncio.sleep(10)

        except asyncio.CancelledError:
            logger.info("BotManager: Получен сигнал остановки...")
            await self._shutdown_all()

    async def _start_bot_process(self, bid: int, bot_data):
        """Инициализирует бота и запускает задачу поллинга."""
        try:
            logger.info(f"🆕 Запуск бота ID {bid} ({bot_data.name})")
            bot = Bot(token=bot_data.token)

            # Удаляем вебхук, если он был установлен (конфликт с поллингом)
            await bot.delete_webhook(drop_pending_updates=True)

            self.active_bots[bid] = bot

            # Запуск в фоне
            task = asyncio.create_task(self._run_isolated_polling(bid, bot))
            self.polling_tasks[bid] = task

            # Приветственное сообщение (опционально)
            # await self._broadcast_system_message(bid, "🚀 **Система запущена!**")

        except Exception as e:
            logger.error(f"Не удалось запустить бота ID {bid}: {e}")
            # Очистка в случае неудачи, чтобы попробовать снова в след. цикле
            if bid in self.active_bots:
                del self.active_bots[bid]

    async def _stop_bot_process(self, bid: int):
        """Корректно останавливает задачу поллинга и очищает ресурсы."""
        logger.info(f"🔻 Остановка бота ID {bid}...")

        # Попытка отправить прощальное сообщение
        # try:
        #     await self._broadcast_system_message(bid, "💤 **Бот остановлен.**")
        # except Exception:
        #     pass

        # Остановка поллинга через Dispatcher
        dp = self.dispatchers.get(bid)
        if dp:
            try:
                await dp.stop_polling()
            except Exception:
                pass

        # Отмена асинхронной задачи
        task = self.polling_tasks.get(bid)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Ошибка при отмене задачи бота {bid}: {e}")
            del self.polling_tasks[bid]

        if bid in self.active_bots:
            del self.active_bots[bid]

        logger.info(f"✅ Бот ID {bid} остановлен.")

    async def _shutdown_all(self):
        """Полная остановка всех ботов при завершении приложения."""
        active_ids = list(self.polling_tasks.keys())
        for bid in active_ids:
            await self._stop_bot_process(bid)
        logger.info("BotManager: Все боты остановлены.")

    async def send_message(self, bot_id: int, chat_id: int, text: str):
        """
        Публичный метод для отправки сообщения конкретному пользователю.

        Args:
            bot_id: ID бота, от имени которого отправлять.
            chat_id: ID чата получателя.
            text: Текст сообщения.
        """
        bot = self.active_bots.get(bot_id)
        if bot:
            try:
                await bot.send_message(chat_id, text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения (Bot: {bot_id}, Chat: {chat_id}): {e}")
        else:
            logger.warning(f"Попытка отправить сообщение через неактивного бота ID {bot_id}.")