"""
Логика главного меню лаунчера (CLI Controller).

Этот модуль управляет основным циклом приложения:
1. Сканирует доступные скрипты в папке `scripts/`.
2. Сопоставляет их с конфигурацией `SCRIPT_HANDLERS`.
3. Отображает интерактивное меню (через `questionary`).
4. Вызывает соответствующие диалоги настройки (`user_prompts`).
5. Запускает выбранную логику (либо как функцию внутри процесса, либо как подпроцесс).

Роль в архитектуре:
    Связующее звено между пользователем (консоль) и бизнес-логикой (Core/Infrastructure).
"""

import os
import sys
import subprocess
from typing import Dict, Any, Callable, Optional

import questionary
from rich.console import Console

from . import user_prompts

from app.infrastructure.storage.data_manager import update_lists_flow, download_data_flow
from app.core.engine.backtest.runners import run_single_backtest_flow, run_batch_backtest_flow
from app.core.engine.optimization.runner import run_optimization_flow
from app.core.engine.live.orchestrator import run_live_monitor_flow
from app.bootstrap.container import container
from app.shared.primitives import ExchangeType
from app.shared.config import config

BASE_DIR = config.BASE_DIR

# --- КОНФИГУРАЦИЯ МАППИНГА ---
# Реестр, связывающий файлы скриптов с логикой их запуска.
# Если скрипта нет в этом списке, он будет показан "как есть" и запущен как внешний процесс.
#
# Structure:
#   "filename.py": {
#       "name": "Отображаемое имя в меню",
#       "prompt_func": Функция, возвращающая dict с настройками (или None при отмене),
#       "dispatcher": Функция, принимающая settings и запускающая логику
#   }

SCRIPT_HANDLERS: Dict[str, Dict[str, Any]] = {
    "manage_data.py": {
        "name": "💾 Управление данными (Data Manager)",
        "prompt_func": user_prompts.prompt_for_data_management,
        "dispatcher": lambda settings: _dispatch_data(settings)
    },
    "run_backtest.py": {
        "name": "🧪 Одиночный Бэктест (Single Backtest)",
        # force_mode="single" гарантирует, что промпт не спросит режим, а сразу перейдет к файлу
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
        "prompt_func": None,  # Нет настроек перед запуском
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

def _dispatch_data(settings: Optional[Dict[str, Any]]):
    """
    Промежуточный диспетчер для управления данными.
    Инициализирует нужного клиента биржи и вызывает соответствующий flow.

    Args:
        settings: Словарь настроек, полученный из user_prompts.
    """
    if not settings:
        return

    # 1. Определяем нужного клиента и режим
    exchange = settings.get("exchange")
    # Логика безопасности: Tinkoff только в песочнице, Bybit - Real (для публичных данных)
    mode = "SANDBOX" if exchange == ExchangeType.TINKOFF else "REAL"

    try:
        # 2. Получаем клиента из контейнера (Singleton/Flyweight)
        client = container.get_exchange_client(exchange, mode=mode)

        action = settings.pop("action")
        if action == "update":
            success, msg = update_lists_flow(settings, client)
            print(msg)
        elif action == "download":
            download_data_flow(settings, client)

    except Exception as e:
        print(f"Ошибка при инициализации клиента биржи: {e}")


def _run_external_script(script_name: str):
    """
    Запускает скрипт как отдельный процесс ОС.

    Используется для:
    1. Изоляции (чтобы ошибка скрипта не крашила лаунчер).
    2. Скриптов с собственным сложным I/O или GUI (Streamlit).
    3. Обхода ограничений GIL для долгих задач (хотя для этого лучше multiprocessing).

    Args:
        script_name: Имя файла в папке scripts/.
    """
    script_path = os.path.join(BASE_DIR, "scripts", script_name)
    print(f"\n--- Запуск скрипта: {script_name} ---\n")

    # Копируем текущее окружение и добавляем корень проекта в PYTHONPATH.
    # Это КРИТИЧНО, иначе скрипт не сможет импортировать пакет 'app'.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    try:
        # sys.executable гарантирует использование того же интерпретатора (venv)
        subprocess.run([sys.executable, script_path], cwd=BASE_DIR, env=env)
    except KeyboardInterrupt:
        print(f"\nСкрипт {script_name} остановлен.")
    except Exception as e:
        print(f"Ошибка при запуске скрипта: {e}")


# --- ОСНОВНАЯ ЛОГИКА ---

def get_scripts_list() -> list:
    """
    Сканирует папку scripts и возвращает список доступных .py файлов.
    """
    scripts_dir = os.path.join(BASE_DIR, "scripts")
    if not os.path.exists(scripts_dir):
        return []

    files = [f for f in os.listdir(scripts_dir) if f.endswith(".py") and f != "__init__.py"]
    return sorted(files)


def main():
    """
    Точка входа в UI Лаунчера.
    Запускает бесконечный цикл меню.
    """
    console = Console()
    console.print("[bold green]Market Bots Launcher[/bold green]", justify="center")

    while True:
        scripts = get_scripts_list()

        # Формирование пунктов меню
        choices = []
        for script_file in scripts:
            handler = SCRIPT_HANDLERS.get(script_file)
            if handler:
                display_name = handler["name"]
            else:
                # Fallback для скриптов без маппинга
                display_name = f"📜 {script_file} (Скрипт)"

            choices.append(questionary.Choice(title=display_name, value=script_file))

        choices.append(questionary.Separator())
        choices.append(questionary.Choice(title="Выход", value="EXIT"))

        # Отображение меню
        selected_script = questionary.select(
            "Выберите действие:",
            choices=choices,
            use_indicator=True
        ).ask()

        if selected_script == "EXIT" or selected_script is None:
            print("До встречи!")
            break

        # Обработка выбора
        handler = SCRIPT_HANDLERS.get(selected_script)

        try:
            if handler:
                # 1. Сценарий с обработчиком
                prompt_func = handler.get("prompt_func")
                dispatch_func = handler.get("dispatcher")

                settings = {}
                if prompt_func:
                    print(f"\n--- Настройка: {selected_script} ---")
                    settings = prompt_func()

                    # Если user_prompts вернул None (пользователь нажал Назад/Отмена)
                    if settings is None:
                        continue

                # Запуск логики
                dispatch_func(settings)

            else:
                # 2. Fallback сценарий (просто запуск файла)
                _run_external_script(selected_script)

            questionary.text("Нажмите Enter, чтобы вернуться в меню...").ask()

        except Exception as e:
            console.print(f"[bold red]Произошла ошибка:[/bold red] {e}")
            # Полный трейсбек полезен для отладки, даже в лаунчере
            import traceback
            traceback.print_exc()
            questionary.text("Нажмите Enter...").ask()


if __name__ == "__main__":
    main()