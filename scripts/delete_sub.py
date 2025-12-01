"""
Скрипт администрирования для удаления подписчиков из базы данных.

Позволяет найти пользователя по Telegram chat_id, просмотреть все его активные
подписки (на разных ботов) и принудительно удалить их из базы.

Используется для ручной чистки базы от неактуальных пользователей или
тестовых аккаунтов.

Запуск:
    python scripts/delete_sub.py
"""

import asyncio
import sys
import os

# Добавляем корень проекта в путь поиска модулей, чтобы видеть пакет app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models import TelegramSubscriber


async def main() -> None:
    """
    Асинхронная точка входа.

    Алгоритм работы:
    1. Запрашивает chat_id у администратора.
    2. Выполняет поиск всех подписок с этим chat_id через ORM.
    3. Выводит список найденных подписок (имена ботов, юзернеймы).
    4. После подтверждения выполняет удаление записей.
    """
    print("🗑️  МАСТЕР УДАЛЕНИЯ ПОДПИСЧИКОВ\n")

    # 1. Ввод и валидация ID
    target_id_str = input("Введите chat_id пользователя для удаления: ").strip()
    if not target_id_str.isdigit():
        print("Ошибка: chat_id должен состоять только из цифр.")
        return

    target_id = int(target_id_str)

    async with async_session_factory() as session:
        # 2. Поиск подписок
        # Используем selectinload для подгрузки связанного объекта бота,
        # чтобы показать его имя в консоли.
        query = (
            select(TelegramSubscriber)
            .options(selectinload(TelegramSubscriber.bot))
            .where(TelegramSubscriber.chat_id == target_id)
        )
        result = await session.execute(query)
        subscribers = result.scalars().all()

        if not subscribers:
            print(f"🤷‍♂️ Пользователь с chat_id {target_id} не найден в базе.")
            return

        # 3. Вывод информации и подтверждение
        print(f"\nНайдено {len(subscribers)} подписок для этого ID:")
        for sub in subscribers:
            bot_name = sub.bot.name if sub.bot else "Unknown Bot"
            print(f" - ID записи: {sub.id} | Юзер: {sub.username} | Бот: {bot_name}")

        confirm = input("\nВы уверены, что хотите УДАЛИТЬ их из базы? (y/n): ").lower()

        if confirm == 'y':
            # 4. Удаление
            # Используем bulk delete запрос для эффективности
            stmt = delete(TelegramSubscriber).where(TelegramSubscriber.chat_id == target_id)
            result = await session.execute(stmt)
            await session.commit()

            print(f"✅ Успешно удалено {result.rowcount} записей.")
        else:
            print("Операция отменена.")


if __name__ == "__main__":
    # Настройка для корректной работы asyncio в Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nПрограмма остановлена пользователем.")