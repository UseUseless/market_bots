import sqlite3
import datetime
import sys
import os

# Добавляем корень проекта в путь, чтобы видеть конфиг
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.shared.config import config


def main():
    db_path = config.DB_PATH

    if not db_path.exists():
        print(f"❌ База данных не найдена по пути: {db_path}")
        return

    con = sqlite3.connect(db_path)
    cursor = con.cursor()

    print(f"🔌 Подключено к БД: {db_path}")

    # 1. Выбираем бота
    cursor.execute("SELECT id, name FROM bot_instances WHERE is_active = 1")
    bots = cursor.fetchall()

    if not bots:
        print("❌ В базе нет активных ботов. Сначала добавьте бота через Лаунчер.")
        return

    print("\n--- Доступные боты ---")
    for b in bots:
        print(f"ID [{b[0]}]: {b[1]}")

    try:
        bot_id_input = input("\nВведите ID бота, к которому добавить друга: ")
        bot_id = int(bot_id_input)
    except ValueError:
        print("Некорректный ID.")
        return

    # Проверка существования бота
    if bot_id not in [b[0] for b in bots]:
        print("Такого ID нет в списке.")
        return

    # 2. Вводим данные друга
    try:
        friend_chat_id = input("Введите chat_id друга (цифры): ")
        chat_id = int(friend_chat_id)
    except ValueError:
        print("Chat ID должен состоять только из цифр.")
        return

    friend_username = input("Введите Username друга (без @, можно пропустить): ") or "Manual_Added"

    # 3. Добавляем в базу
    try:
        now = datetime.datetime.utcnow().isoformat()

        # Проверяем, нет ли уже такого
        cursor.execute("SELECT id FROM telegram_subscribers WHERE bot_id = ? AND chat_id = ?", (bot_id, chat_id))
        exists = cursor.fetchone()

        if exists:
            print("⚠️ Этот пользователь уже есть в базе! Обновляю статус на Active.")
            cursor.execute("UPDATE telegram_subscribers SET is_active = 1 WHERE id = ?", (exists[0],))
        else:
            cursor.execute("""
                INSERT INTO telegram_subscribers (bot_id, chat_id, username, is_active, created_at)
                VALUES (?, ?, ?, 1, ?)
            """, (bot_id, chat_id, friend_username, now))
            print(f"✅ Пользователь {friend_username} ({chat_id}) успешно добавлен!")

        con.commit()

    except Exception as e:
        print(f"❌ Ошибка SQL: {e}")
    finally:
        con.close()


if __name__ == "__main__":
    main()