import asyncio
import sys
import os
import questionary

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.storage.database import async_session_factory
from app.storage.repositories import ConfigRepository
from app.storage.models import StrategyConfig
from sqlalchemy import select


async def main():
    print("--- Мастер настройки Ботов ---")

    # ИСПОЛЬЗУЕМ ask_async() ЧТОБЫ НЕ КОНФЛИКТОВАТЬ С ASYNCIO
    bot_token = await questionary.password("Введите токен бота (от @BotFather):").ask_async()
    if not bot_token: return

    bot_name = await questionary.text("Придумайте имя для этого бота (внутреннее):").ask_async()

    async with async_session_factory() as session:
        repo = ConfigRepository(session)

        # Создаем бота
        try:
            bot = await repo.create_bot(name=bot_name, token=bot_token)
            print(f"✅ Бот '{bot.name}' создан с ID {bot.id}.")
        except Exception as e:
            print(f"Ошибка создания бота (возможно, имя не уникально): {e}")
            return

        # Привяжем к нему существующие стратегии?
        query = select(StrategyConfig).where(StrategyConfig.bot_id == None)
        result = await session.execute(query)
        orphaned_strategies = result.scalars().all()

        if orphaned_strategies:
            print(f"Найдено бесхозных стратегий: {len(orphaned_strategies)}")
            should_bind = await questionary.confirm("Привязать первую найденную стратегию к этому боту?").ask_async()

            if should_bind:
                orphaned_strategies[0].bot_id = bot.id
                await session.commit()
                print("✅ Стратегия привязана.")


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())