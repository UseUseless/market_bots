"""
Логика главного меню лаунчера (CLI Controller).

Этот модуль управляет основным циклом приложения.
Теперь он работает как "тонкий клиент": собирает настройки через UI
и запускает соответствующие скрипты из папки scripts/ как подпроцессы.
"""

import os
import sys
import subprocess
from typing import Dict, Any, List

import questionary
from rich.console import Console

from . import dialogs
from app.shared.config import config

BASE_DIR = config.BASE_DIR

# Ключи, которые используются только внутри UI диалогов и не должны попадать в CLI аргументы
INTERNAL_KEYS = {"mode", "confirm"}


def _build_cli_args(settings: Dict[str, Any], positional_key: str = None) -> List[str]:
    """
    Преобразует словарь настроек в список аргументов командной строки.

    Args:
        settings: Словарь параметров (например, {"exchange": "bybit", "days": 100}).
        positional_key: Если задан, значение этого ключа будет добавлено
                        как позиционный аргумент (без --флага) в начало.
                        Нужно для команд типа 'manage_data.py download ...'.

    Returns:
        List[str]: Список аргументов (['download', '--exchange', 'bybit', ...]).
    """
    args = []

    # 1. Обработка позиционного аргумента (команды)
    if positional_key and positional_key in settings:
        args.append(str(settings.pop(positional_key)))

    # 2. Обработка остальных аргументов
    for key, value in settings.items():
        # Пропускаем служебные ключи или пустые значения
        if value is None or key in INTERNAL_KEYS:
            continue

        # Преобразуем python-ключи в CLI-флаги (risk_manager_type -> --rm)
        # Маппинг ключей, если они отличаются в user_prompts и в скриптах
        flag = f"--{key.replace('_', '-')}"

        # Специфичный маппинг для некоторых аргументов, где имена не совпадают
        if key == "risk_manager_type":
            flag = "--rm"
        elif key == "portfolio_path":
            flag = "--portfolio-path"

        if isinstance(value, bool):
            # Для булевых флагов (если True, то добавляем флаг, если False - нет)
            if value:
                args.append(flag)
        elif isinstance(value, list):
            # Для списков (nargs='+')
            args.append(flag)
            args.extend([str(v) for v in value])
        else:
            # Для обычных значений
            args.append(flag)
            args.append(str(value))

    return args


def _run_script(script_name: str, args: List[str] = None):
    """
    Запускает скрипт из папки scripts/ с переданными аргументами.
    """
    script_path = os.path.join(BASE_DIR, "scripts", script_name)
    cmd = [sys.executable, script_path] + (args or [])

    print(f"\n🚀 Запуск: python scripts/{script_name} {' '.join(args or [])}")
    print("-" * 50 + "\n")

    # Копируем окружение и настраиваем PYTHONPATH
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")

    try:
        subprocess.run(cmd, cwd=BASE_DIR, env=env)
    except KeyboardInterrupt:
        print(f"\n🛑 Скрипт {script_name} остановлен пользователем.")
    except Exception as e:
        print(f"❌ Ошибка при запуске: {e}")


# --- КОНФИГУРАЦИЯ ---

# Описываем, как запускать каждый скрипт:
# - prompt_func: функция диалога
# - positional_arg: какой ключ из настроек является командой (для manage_data)
SCRIPT_CONFIG = {
    "manage_data.py": {
        "name": "💾 Управление данными (Data Manager)",
        "prompt_func": dialogs.prompt_for_data_management,
        "positional_arg": "action"  # 'action' из промпта станет командой (update/download)
    },
    "run_backtest.py": {
        "name": "🧪 Одиночный Бэктест",
        "prompt_func": lambda: dialogs.prompt_for_backtest_settings(force_mode="single"),
    },
    "run_batch_backtest.py": {
        "name": "📦 Пакетный Бэктест",
        "prompt_func": lambda: dialogs.prompt_for_backtest_settings(force_mode="batch"),
    },
    "run_optimization.py": {
        "name": "🧬 Оптимизация (WFO)",
        "prompt_func": dialogs.prompt_for_optimization_settings,
    },
    "run_signals.py": {
        "name": "📡 Монитор Сигналов (Live)",
        "prompt_func": dialogs.prompt_for_live_settings,
    },
    "run_dashboard.py": {
        "name": "📊 Дашборд (Web UI)",
        "prompt_func": None,
    },
    "add_bot.py": {
        "name": "🤖 Добавить Телеграм Бота",
        "prompt_func": None,
    },
    "init_db.py": {
        "name": "🛠️ Инициализация БД",
        "prompt_func": lambda: {"confirm": dialogs.ask(questionary.confirm, "Создать таблицы?")},
    }
}


def get_scripts_list() -> list:
    scripts_dir = os.path.join(BASE_DIR, "scripts")
    if not os.path.exists(scripts_dir):
        return []
    return sorted([f for f in os.listdir(scripts_dir) if f.endswith(".py") and f != "__init__.py"])


def main():
    console = Console()
    console.print("[bold green]Market Bots Launcher[/bold green]", justify="center")

    while True:
        scripts = get_scripts_list()
        choices = []

        for script_file in scripts:
            config_entry = SCRIPT_CONFIG.get(script_file)
            display_name = config_entry["name"] if config_entry else f"📜 {script_file}"
            choices.append(questionary.Choice(title=display_name, value=script_file))

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

        # Обработка выбора
        config_entry = SCRIPT_CONFIG.get(selected_script)
        cli_args = []

        try:
            if config_entry:
                # 1. Запуск диалога (если есть)
                prompt_func = config_entry.get("prompt_func")
                if prompt_func:
                    print(f"\n--- Настройка: {selected_script} ---")
                    settings = prompt_func()

                    # Если пользователь нажал "Назад" или отменил
                    if settings is None:
                        continue

                    # Специальная проверка для init_db (подтверждение)
                    if selected_script == "init_db.py" and not settings.get("confirm"):
                        print("Отмена.")
                        continue
                    if selected_script == "init_db.py":
                        settings = {} # Очищаем, чтобы не передавать --confirm как аргумент

                    # 2. Генерация аргументов
                    pos_key = config_entry.get("positional_arg")
                    cli_args = _build_cli_args(settings, positional_key=pos_key)

            # 3. Запуск скрипта
            _run_script(selected_script, cli_args)

            questionary.text("Нажмите Enter, чтобы вернуться в меню...").ask()

        except Exception as e:
            console.print(f"[bold red]Произошла ошибка в лаунчере:[/bold red] {e}")
            import traceback
            traceback.print_exc()
            questionary.text("Нажмите Enter...").ask()


if __name__ == "__main__":
    main()