import questionary
import os
import subprocess
from typing import Dict, Any, Callable, Tuple, Optional

from app.flows.backtest_flow import run_single_backtest_flow
from app.flows.batch_backtest_flow import run_batch_backtest_flow
from app.flows.optimization_flow import run_optimization_flow
from app.flows.live_flow import run_live_flow
from app.flows.data_management_flow import update_lists_flow, download_data_flow

from . import user_prompts

def dispatch_data_management(settings: Dict[str, Any]):
    """Вызывает нужный flow для управления данными."""
    action = settings.pop("action")

    if action == "update":
        print(f"\nЗапускаю обновление для {settings['exchange'].upper()}...")
        success, message = update_lists_flow(settings)
        style = "bold green" if success else "bold red"
        questionary.print(f"\n{message}", style=style)

    elif action == "download":
        print("\nЗапускаю скачивание...\n")
        download_data_flow(settings)
        questionary.print("\nПроцесс скачивания завершен. Проверьте логи и папку /data.", style="bold blue")


def dispatch_backtest(settings: Dict[str, Any]):
    """Вызывает нужный flow для бэктеста (одиночный или пакетный)."""
    mode = settings.pop("mode")
    if mode == "single":
        print("\nЗапускаю бэктест...")
        run_single_backtest_flow(settings)
    elif mode == "batch":
        print("\nЗапускаю массовый бэктест...")
        run_batch_backtest_flow(settings)
    questionary.print("\nОперация завершена. Смотрите отчеты в папках /reports и /logs.", style="bold blue")


def dispatch_optimization(settings: Dict[str, Any]):
    """Вызывает flow для оптимизации."""
    print("\nЗапускаю Walk-Forward Optimizer... Это может занять много времени.")
    run_optimization_flow(settings)
    print("\nОптимизация завершена.")


def dispatch_live_trading(settings: Dict[str, Any]):
    """Вызывает flow для live-торговли."""
    print("\nЗапускаю live-бота... Нажмите Ctrl+C, чтобы остановить.")
    run_live_flow(settings)
    print("\nLive-сессия завершена.")


def run_dashboard():
    """Запускает Streamlit дашборд как внешний процесс."""
    print("\n--- Запуск панели анализа (Dashboard) ---\n")
    dashboard_path = os.path.join("app", "ui", "dashboard", "main.py")
    command = ["streamlit", "run", dashboard_path]
    print("Чтобы остановить дашборд, нажмите Ctrl+C в этом окне.")
    try:
        subprocess.run(command)
    except FileNotFoundError:
        questionary.print("\n[Ошибка] Команда 'streamlit' не найдена.", style="bold red")
        questionary.print("Убедитесь, что Streamlit установлен и доступен в вашем окружении (pip install streamlit).")
    except Exception as e:
        questionary.print(f"\nНе удалось запустить дашборд: {e}", style="bold red")

MENU_CONFIG: Dict[str, Optional[Tuple[Optional[Callable], Optional[Callable]]]] = {
    "1. Управление данными (скачать/обновить)": (user_prompts.prompt_for_data_management, dispatch_data_management),
    "-----------------------------------------": None,
    "2. Запустить бэктест": (user_prompts.prompt_for_backtest_settings, dispatch_backtest),
    "3. Проанализировать результаты (Dashboard)": (None, run_dashboard),
    "4. Запустить оптимизацию параметров (WFO)": (user_prompts.prompt_for_optimization_settings, dispatch_optimization),
    "------------------------------------------": None,
    "5. Запустить симуляцию в 'песочнице'": (user_prompts.prompt_for_live_settings, dispatch_live_trading),
    "Выход": ("EXIT", None)
}

def main():
    """Отображает главное меню и управляет вызовами на основе MENU_CONFIG."""
    while True:
        choices = [questionary.Separator(k) if v is None else k for k, v in MENU_CONFIG.items()]

        try:
            choice_str = questionary.select("Главное меню:", choices=choices, use_indicator=True).ask()
            if choice_str is None:
                print("\nЗавершение работы.")
                break

            action_config = MENU_CONFIG.get(choice_str)
            if not action_config or action_config[0] == "EXIT":
                print("\nЗавершение работы.")
                break

            prompt_func, dispatch_func = action_config

            settings = None
            if prompt_func:
                print(f"\n--- {choice_str.split('. ')[1]} ---")
                settings = prompt_func()

            # Если settings - это словарь, значит, пользователь успешно прошел диалог.
            if isinstance(settings, dict):
                dispatch_func(settings)
                # Используем questionary.text для паузы, чтобы избежать конфликтов с консолью
                questionary.text("Нажмите Enter, чтобы вернуться в главное меню...").ask()

            # Если это действие без диалога (например, дашборд)
            elif prompt_func is None and dispatch_func:
                dispatch_func()
                questionary.text("Нажмите Enter, чтобы вернуться в главное меню...").ask()

        except (user_prompts.UserCancelledError, KeyboardInterrupt):
            # Обработка явной отмены пользователем (Ctrl+C)
            questionary.print("\n\nОперация отменена пользователем.", style="bold yellow")
            questionary.text("Нажмите Enter, чтобы вернуться в главное меню...").ask()
        except Exception as e:
            questionary.print(f"\nПроизошла критическая ошибка в главном цикле: {e}\n", style="bold red")
            import traceback
            traceback.print_exc()
            questionary.text("Нажмите Enter, чтобы вернуться в главное меню...").ask()