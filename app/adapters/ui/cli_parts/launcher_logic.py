import os
import sys
import subprocess
import questionary
from rich.console import Console

from config import BASE_DIR
from . import user_prompts

from app.services.data_provider.management import update_lists_flow, download_data_flow
from app.engines.backtest.flows.single import run_single_backtest_flow
from app.engines.backtest.flows.batch import run_batch_backtest_flow
from app.engines.backtest.flows.optimization import run_optimization_flow
from app.engines.live.orchestrator import run_live_monitor_flow

# --- КОНФИГУРАЦИЯ МАППИНГА ---
# Связываем имя файла скрипта с красивым названием и логикой UI.
# Если скрипта нет в этом списке, он будет показан "как есть" и запущен без аргументов.

SCRIPT_HANDLERS = {
    "manage_data.py": {
        "name": "💾 Управление данными (Data Manager)",
        "prompt_func": user_prompts.prompt_for_data_management,
        "dispatcher": lambda settings: _dispatch_data(settings)
    },
    "run_backtest.py": {
        "name": "🧪 Одиночный Бэктест (Single Backtest)",
        # Мы используем существующий промпт, но нам нужно убедиться, что он возвращает режим 'single'
        "prompt_func": lambda: user_prompts.prompt_for_backtest_settings(force_mode="single"),
        "dispatcher": run_single_backtest_flow
    },
    "run_batch_backtest.py": {
        "name": "📦 Пакетный Бэктест (Batch Backtest)",
        "prompt_func": lambda: user_prompts.prompt_for_backtest_settings(force_mode="batch"),
        "dispatcher": run_batch_backtest_flow
    },
    "run_optimization.py": {
        "name": "🧬 Оптимизация (WFO / Optuna)",
        "prompt_func": user_prompts.prompt_for_optimization_settings,
        "dispatcher": run_optimization_flow
    },
    "run_dashboard.py": {
        "name": "📊 Дашборд (Аналитика & Результаты)",
        "prompt_func": None,  # Нет вопросов перед запуском
        "dispatcher": lambda _: _run_external_script("run_dashboard.py")
    },
    "run_signals.py": {
        "name": "📡 Монитор Сигналов (Telegram Alerts)",
        "prompt_func": user_prompts.prompt_for_live_settings,
        "dispatcher": run_live_monitor_flow
    },
    "add_bot.py": {
        "name": "🤖 Добавить Телеграм Бота (Wizard)",
        "prompt_func": None,  # Скрипт сам внутри себя задает вопросы
        "dispatcher": lambda _: _run_external_script("add_bot.py")
    },
    "init_db.py": {
        "name": "🛠️ Инициализация Базы Данных",
        "prompt_func": lambda: questionary.confirm("Пересоздать/Обновить таблицы БД?").ask(),
        "dispatcher": lambda confirmed: _run_external_script("init_db.py") if confirmed else print("Отмена.")
    }
}


# --- Вспомогательные функции диспетчеров ---

def _dispatch_data(settings):
    """Разруливает логику manage_data, так как там два действия."""
    if not settings: return
    action = settings.pop("action")
    if action == "update":
        success, msg = update_lists_flow(settings)
        print(msg)
    elif action == "download":
        download_data_flow(settings)


def _run_external_script(script_name: str):
    """Запускает скрипт как отдельный процесс (для изоляции)."""
    script_path = os.path.join(BASE_DIR, "scripts", script_name)
    print(f"\n--- Запуск скрипта: {script_name} ---\n")

    # копируем текущее окружение и добавляем корень проекта в PYTHONPATH
    env = os.environ.copy()
    # Добавляем BASE_DIR (где лежит папка app) в пути поиска питона
    env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")

    try:
        # Используем текущий интерпретатор Python
        subprocess.run([sys.executable, script_path], cwd=BASE_DIR)
    except KeyboardInterrupt:
        print(f"\nСкрипт {script_name} остановлен.")
    except Exception as e:
        print(f"Ошибка при запуске скрипта: {e}")


# --- ОСНОВНАЯ ЛОГИКА ---

def get_scripts_list():
    """Сканирует папку scripts и возвращает список файлов."""
    scripts_dir = os.path.join(BASE_DIR, "scripts")
    if not os.path.exists(scripts_dir):
        return []

    files = [f for f in os.listdir(scripts_dir) if f.endswith(".py") and f != "__init__.py"]
    return sorted(files)


def main():
    console = Console()
    console.print("[bold green]Market Bots Launcher[/bold green]", justify="center")

    while True:
        scripts = get_scripts_list()

        # Формируем меню
        choices = []
        mapped_keys = []

        for script_file in scripts:
            handler = SCRIPT_HANDLERS.get(script_file)
            if handler:
                display_name = handler["name"]
            else:
                display_name = f"📜 {script_file} (Скрипт)"

            choices.append(questionary.Choice(title=display_name, value=script_file))
            mapped_keys.append(script_file)

        choices.append(questionary.Separator())
        choices.append(questionary.Choice(title="Выход", value="EXIT"))

        selected_script = questionary.select(
            "Выберите действие:",
            choices=choices,
            use_indicator=True
        ).ask()

        if selected_script == "EXIT" or selected_script is None:
            print("До встречи!")
            break

        # Логика запуска
        handler = SCRIPT_HANDLERS.get(selected_script)

        try:
            if handler:
                # 1. Есть специальный обработчик
                prompt_func = handler.get("prompt_func")
                dispatch_func = handler.get("dispatcher")

                settings = {}
                if prompt_func:
                    print(f"\n--- Настройка: {selected_script} ---")
                    settings = prompt_func()
                    if settings is None and prompt_func is not None:
                        # Пользователь нажал "Назад" или отменил
                        continue

                        # Запуск функции
                dispatch_func(settings)

            else:
                # 2. Нет обработчика - просто запускаем файл
                _run_external_script(selected_script)

            questionary.text("Нажмите Enter, чтобы вернуться в меню...").ask()

        except Exception as e:
            console.print(f"[bold red]Произошла ошибка:[/bold red] {e}")
            import traceback
            traceback.print_exc()
            questionary.text("Нажмите Enter...").ask()


if __name__ == "__main__":
    main()