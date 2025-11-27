import os
import subprocess
import sys

from app.shared.config import config
BASE_DIR = config.BASE_DIR

def main():
    """Запускает Streamlit Dashboard."""
    dashboard_path = os.path.join(BASE_DIR, "app", "adapters", "dashboard", "main.py")

    print(f"Запуск дашборда из: {dashboard_path}")

    if not os.path.exists(dashboard_path):
        print(f"❌ ОШИБКА: Файл не найден: {dashboard_path}")
        return

    # Streamlit запускается как отдельный процесс
    try:
        # Используем sys.executable, чтобы гарантировать запуск в том же venv
        subprocess.run([sys.executable, "-m", "streamlit", "run", dashboard_path], cwd=BASE_DIR, check=True)
    except KeyboardInterrupt:
        print("\nДашборд остановлен.")
    except Exception as e:
        print(f"Ошибка запуска: {e}")


if __name__ == "__main__":
    main()