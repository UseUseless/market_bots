"""
Скрипт для регистрации нового Telegram-бота и добавления его в базу данных.

Запуск:
    python scripts/add_bot.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import questionary

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import ConfigRepository
from app.shared.decorators import safe_entry

@safe_entry
async def main() -> None:
    """
    Мастер настройки:
    1. Запрашивает у пользователя токен и имя бота.
    2. Сохраняет бота в базу данных.
    """
    print("--- Мастер настройки Ботов ---")

    # Используем ask_async(), чтобы ввод не блокировал событийный цикл asyncio.
    bot_token = await questionary.password("Введите токен бота (от @BotFather):").ask_async()
    if not bot_token:
        return

    bot_name = await questionary.text("Придумайте имя для этого бота (внутреннее):").ask_async()

    async with async_session_factory() as session:
        repo = ConfigRepository(session)

        # --- Создание бота ---
        try:
            bot = await repo.create_bot(name=bot_name, token=bot_token)
            print(f"\n✅ Бот '{bot.name}' успешно создан с ID {bot.id}.")
            print("Теперь вы можете привязать к нему стратегии через Дашборд.")

        except Exception as e:
            print(f"\n❌ Не удалось создать бота: {e}")
            print("Возможно, имя или токен уже используются.")


if __name__ == "__main__":
    main()
