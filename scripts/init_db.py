"""
Скрипт инициализации базы данных.

Отвечает за создание всех таблиц, определенных в SQLAlchemy моделях (`app.infrastructure.database.models`).

Запуск:
    python scripts/init_db.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.database.session import init_models
from app.shared.decorators import safe_entry

@safe_entry
async def main() -> None:
    """
    Вызывает `init_models()` для создания таблиц (аналог CREATE TABLE IF NOT EXISTS).
    """
    print("🚀 Начало инициализации базы данных...")

    # Создание схемы БД
    await init_models()

    print("✅ Таблицы успешно созданы (или уже существовали).")
    print("🏁 Инициализация завершена. Теперь переходите к настройке через Дашборд.")


if __name__ == "__main__":
    main()