"""
Скрипт для просмотра списка подписчиков.

Выводит в консоль сводную таблицу всех пользователей, подписанных на
телеграм-ботов системы. Использует Pandas для форматирования вывода.

Запуск:
    python scripts/list_subs.py
"""

import asyncio
import sys
import os
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import selectinload

# Добавляем корень проекта в путь поиска модулей
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.models import TelegramSubscriber


async def main() -> None:
    """
    Асинхронная точка входа.

    Алгоритм работы:
    1. Подключается к БД через SQLAlchemy сессию.
    2. Загружает всех подписчиков вместе со связанными ботами (Eager Loading).
    3. Преобразует объекты ORM в список словарей для Pandas.
    4. Выводит отформатированную таблицу в консоль.
    """
    print("📂 Чтение базы данных...\n")

    async with async_session_factory() as session:
        # Формируем запрос с подгрузкой связанной сущности 'bot',
        # чтобы получить имя бота без дополнительных запросов.
        query = (
            select(TelegramSubscriber)
            .options(selectinload(TelegramSubscriber.bot))
            .order_by(TelegramSubscriber.created_at.desc())
        )

        result = await session.execute(query)
        subscribers = result.scalars().all()

        if not subscribers:
            print("📭 Список подписчиков пуст.")
            return

        # Преобразуем объекты SQLAlchemy в плоский список словарей для DataFrame
        data = []
        for sub in subscribers:
            data.append({
                "Bot Name": sub.bot.name if sub.bot else "Unknown",
                "Username": sub.username,
                "First Name": sub.first_name,
                "Chat ID": sub.chat_id,
                "Active": "✅" if sub.is_active else "❌",
                "Created At": sub.created_at.strftime("%Y-%m-%d %H:%M")
            })

        # Создаем DataFrame для красивой визуализации
        df = pd.read_json(pd.Series(data).to_json(orient='records'), orient='records')

        # Или более прямой способ, если версия pandas позволяет:
        # df = pd.DataFrame(data)

        print(df.to_string(index=False))
        print(f"\nВсего подписчиков: {len(df)}")


if __name__ == "__main__":
    # Настройка для Windows
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nПросмотр завершен.")