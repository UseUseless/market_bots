import questionary
import os
import subprocess
from typing import Dict, Any, Callable, Tuple, Optional

# --- Импорты бизнес-логики из app/flows ---
from app.flows.backtest_flow import run_single_backtest_flow
from app.flows.batch_backtest_flow import run_batch_backtest_flow
from app.flows.optimization_flow import run_optimization_flow
from app.flows.live_flow import run_live_flow
from app.flows.data_management_flow import update_lists_flow, download_data_flow

# --- Импорты логики UI ---
from . import user_prompts


def dispatch_data_management(settings: Dict[str, Any]):
    """Вызывает нужный flow для управления данными."""
    action = settings.pop("action")
    if action == "update":
        print(f"\nЗапускаю обновление для {settings['exchange'].upper()}...\n")
        update_lists_flow(settings)
    elif action == "download":
        print("\nЗапускаю скачивание...\n")
        download_data_flow(settings)

def dispatch_backtest(settings: Dict[str, Any]):
    """Вызывает нужный flow для бэктеста (одиночный или пакетный)."""
    mode = settings.pop("mode")
    if mode == "single":
        print("\nЗапускаю бэктест...")
        run_single_backtest_flow(settings)
    elif mode == "batch":
        print("\nЗапускаю массовый бэктест...")
        run_batch_backtest_flow(settings)

def dispatch_optimization(settings: Dict[str, Any]):
    """Вызывает flow для оптимизации."""
    print("\nЗапускаю Walk-Forward Optimizer... Это может занять много времени.")
    run_optimization_flow(settings)

def dispatch_live_trading(settings: Dict[str, Any]):
    """Вызывает flow для live-торговли."""
    print("\nЗапускаю live-бота... Нажмите Ctrl+C, чтобы остановить.")
    run_live_flow(settings)

def run_dashboard():
    """Запускает Streamlit дашборд как внешний процесс."""
    print("\n--- Запуск панели анализа (Dashboard) ---\n")
    dashboard_path = os.path.join("app", "ui", "dashboard", "main.py")
    command = ["streamlit", "run", dashboard_path]
    print("Чтобы остановить дашборд, нажмите Ctrl+C в этом окне.")
    try:
        subprocess.run(command)
    except FileNotFoundError:
        print("\n[Ошибка] Команда 'streamlit' не найдена.")
        print("Убедитесь, что Streamlit установлен и доступен в вашем окружении (pip install streamlit).")
    except Exception as e:
        print(f"\nНе удалось запустить дашборд: {e}")

# Конфигурация меню связывает название пункта с функцией-промптером и функцией-диспетчером.
# 'None' используется для разделителей или пунктов без логики.
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
            if choice_str is None: # Обработка Ctrl+C на самом меню
                print("Завершение работы.")
                break

            action_config = MENU_CONFIG.get(choice_str)
            if not action_config or action_config[0] == "EXIT":
                print("Завершение работы.")
                break

            prompt_func, dispatch_func = action_config

            settings = None
            if prompt_func:
                # Запускаем диалог для сбора настроек
                print(f"\n--- {choice_str.split('. ')[1]} ---")
                settings = prompt_func()

            if settings is not None:
                # Если диалог прошел успешно, запускаем соответствующий диспетчер
                dispatch_func(settings)
                input("\nНажмите Enter, чтобы вернуться в главное меню...")
            elif prompt_func is None and dispatch_func:
                # Для пунктов без диалога, как Dashboard
                dispatch_func()
            else:
                # Если пользователь отменил диалог (settings is None)
                print("\nОперация отменена. Возврат в главное меню.")

        except (user_prompts.UserCancelledError, KeyboardInterrupt):
            print("\nОперация отменена. Возврат в главное меню.")
        except Exception as e:
            print(f"\nПроизошла критическая ошибка в главном цикле: {e}\n")
            import traceback
            traceback.print_exc()
            input("\nНажмите Enter, чтобы вернуться в главное меню...")