import sqlite3
import sys
import os

# Магия для импорта конфига из корня
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.shared.config import config


def main():
    db_path = config.DB_PATH
    if not db_path.exists():
        print(f"❌ БД не найдена: {db_path}")
        return

    con = sqlite3.connect(db_path)
    cursor = con.cursor()

    print("🗑️  МАСТЕР УДАЛЕНИЯ ПОДПИСЧИКОВ\n")

    # 1. Ввод ID
    target_id = input("Введите chat_id пользователя для удаления: ").strip()
    if not target_id.isdigit():
        print("Ошибка: chat_id должен быть числом.")
        return

    # 2. Поиск жертвы
    cursor.execute("""
        SELECT t.id, t.username, t.first_name, b.name 
        FROM telegram_subscribers t
        JOIN bot_instances b ON t.bot_id = b.id
        WHERE t.chat_id = ?
    """, (target_id,))

    rows = cursor.fetchall()

    if not rows:
        print(f"🤷‍♂️ Пользователь с chat_id {target_id} не найден в базе.")
        return

    # 3. Подтверждение
    print(f"\nНайдено {len(rows)} подписок для этого ID:")
    for row in rows:
        print(f" - ID записи: {row[0]} | Юзер: {row[1]} | Бот: {row[3]}")

    confirm = input("\nВы уверены, что хотите УДАЛИТЬ их из базы? (y/n): ").lower()

    if confirm == 'y':
        # 4. Удаление
        cursor.execute("DELETE FROM telegram_subscribers WHERE chat_id = ?", (target_id,))
        con.commit()
        print(f"✅ Успешно удалено {cursor.rowcount} записей.")
    else:
        print("Операция отменена.")

    con.close()


if __name__ == "__main__":
    main()