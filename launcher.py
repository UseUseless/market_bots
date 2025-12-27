"""
Вход в приложение (Launcher).

Этот скрипт запускает интерактивное CLI-меню.
Через него пользователь получает доступ ко всем скриптам.

Запуск:
    python launcher.py
"""

import sys
import os

# Добавляем текущую директорию (корень проекта) в начало sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.adapters.cli.menu import main as start_launcher_ui
from app.shared.decorators import safe_entry

@safe_entry
def main() -> None:
    """
    Запуск консольного лаунчера
    """
    start_launcher_ui()

if __name__ == "__main__":
    main()