import os
import subprocess

from config import BASE_DIR


def main():
    """Запускает Streamlit Dashboard."""
    dashboard_path = os.path.join(BASE_DIR, "app", "adapters", "ui", "dashboard", "main.py")
    print(f"Запуск дашборда из: {dashboard_path}")

    # Streamlit запускается как отдельный процесс
    try:
        subprocess.run(["streamlit", "run", dashboard_path], cwd=BASE_DIR, check=True)
    except KeyboardInterrupt:
        print("\nДашборд остановлен.")
    except Exception as e:
        print(f"Ошибка запуска: {e}")


if __name__ == "__main__":
    main()