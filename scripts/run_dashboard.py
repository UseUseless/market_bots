"""
Скрипт запуска веб-интерфейса (Dashboard).

Этот скрипт выступает оберткой для запуска Streamlit приложения.
Дашборд позволяет визуализировать результаты бэктестов, сравнивать стратегии
и управлять конфигурацией ботов.

Запуск:
    python scripts/run_dashboard.py
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.shared.config import config
from app.shared.decorators import safe_entry

BASE_DIR = config.BASE_DIR


@safe_entry
def main() -> None:
    """
    Запускает Streamlit приложение как подпроцесс.

    Алгоритм:
    1. Определяет путь к `main.py` дашборда.
    2. Проверяет существование этого файла.
    3. Запускает `streamlit run` в текущем окружении Python.
    """
    dashboard_path = os.path.join(BASE_DIR, "app", "adapters", "dashboard", "main.py")

    print(f"🚀 Инициализация дашборда...")

    if not os.path.exists(dashboard_path):
        raise FileNotFoundError(f"Файл дашборда не найден: {dashboard_path}")

    print("🌐 Запуск Streamlit сервера... (Нажмите Ctrl+C для остановки)")

    # Streamlit запускается как отдельный процесс
    try:
        # Запускаем Streamlit.
        # sys.executable гарантирует использование того же python (venv), что и этот скрипт.
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", dashboard_path],
            cwd=BASE_DIR,
            check=True
        )

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Дашборд завершился с кодом ошибки {e.returncode}.")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()