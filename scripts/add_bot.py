"""
Скрипт-мастер для добавления нового Telegram-бота в базу данных.

Этот модуль предоставляет интерактивный интерфейс командной строки (CLI)
для регистрации токенов ботов и их имен. Также он предлагает
автоматически привязать существующие "бесхозные" стратегии к новому боту.

Запуск:
    python scripts/add_bot.py
"""

import asyncio
import sys
import os

# Это позволяет видеть пакет 'app', даже если скрипт запущен напрямую из папки scripts
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import questionary
from sqlalchemy import select

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import ConfigRepository
from app.infrastructure.database.models import StrategyConfig


async def main() -> None:
    """
    Асинхронная точка входа в мастер настройки.

    Выполняет следующие шаги:
    1. Запрашивает у пользователя токен и имя бота.
    2. Сохраняет бота в базу данных через репозиторий.
    3. Проверяет наличие стратегий без привязанного бота.
    4. Предлагает привязать найденную стратегию к созданному боту.
    """
    print("--- Мастер настройки Ботов ---")

    # Используем ask_async(), чтобы ввод не блокировал событийный цикл asyncio.
    # Это критично, так как внутри уже запущен loop.
    bot_token = await questionary.password("Введите токен бота (от @BotFather):").ask_async()
    if not bot_token:
        return

    bot_name = await questionary.text("Придумайте имя для этого бота (внутреннее):").ask_async()

    async with async_session_factory() as session:
        repo = ConfigRepository(session)

        # --- 1. Создание бота ---
        try:
            bot = await repo.create_bot(name=bot_name, token=bot_token)
            print(f"✅ Бот '{bot.name}' создан с ID {bot.id}.")
        except Exception as e:
            # Частая ошибка: нарушение уникальности имени или токена (IntegrityError)
            print(f"Ошибка создания бота (возможно, имя или токен уже используются): {e}")
            return

        # --- 2. Поиск и привязка стратегий ---
        # Ищем стратегии, у которых поле bot_id равно NULL
        query = select(StrategyConfig).where(StrategyConfig.bot_id.is_(None))
        result = await session.execute(query)
        orphaned_strategies = result.scalars().all()

        if orphaned_strategies:
            print(f"Найдено бесхозных стратегий: {len(orphaned_strategies)}")

            # Простой сценарий: предлагаем привязать первую попавшуюся.
            # В будущем здесь можно сделать multiselect список.
            should_bind = await questionary.confirm(
                "Привязать первую найденную стратегию к этому боту?"
            ).ask_async()

            if should_bind:
                # SQLAlchemy отслеживает изменения объектов.
                # Достаточно изменить атрибут и сделать commit.
                orphaned_strategies[0].bot_id = bot.id
                await session.commit()
                print("✅ Стратегия успешно привязана.")


if __name__ == "__main__":
    # Специальная настройка для Windows, необходимая для корректной работы
    # асинхронных операций (особенно с БД и сокетами) в Python 3.8+.
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОтменено пользователем.")