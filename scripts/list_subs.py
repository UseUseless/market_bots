import sqlite3
import sys
import os
import pandas as pd

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.shared.config import config


def main():
    db_path = config.DB_PATH
    if not db_path.exists():
        print(f"❌ База данных не найдена: {db_path}")
        return

    con = sqlite3.connect(db_path)

    print(f"📂 Чтение базы: {db_path}\n")

    query = """
    SELECT 
        b.name as bot_name,
        t.username,
        t.first_name,
        t.chat_id,
        t.is_active,
        t.created_at
    FROM telegram_subscribers t
    JOIN bot_instances b ON t.bot_id = b.id
    """

    try:
        df = pd.read_sql(query, con)
        if df.empty:
            print("📭 Список подписчиков пуст.")
        else:
            # Красивый вывод таблицы
            print(df.to_string(index=False))
            print(f"\nВсего подписчиков: {len(df)}")
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()