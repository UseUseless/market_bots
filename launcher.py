import questionary
import subprocess
import sys
import os
from typing import List, Dict, Type

from strategies.base_strategy import BaseStrategy
from config import PATH_CONFIG, EXCHANGE_INTERVAL_MAPS

# --- Константа для опции "Назад" ---
GO_BACK_OPTION = "Назад"

def get_available_strategies() -> Dict[str, Type[BaseStrategy]]:
    """Динамически находит и импортирует все доступные стратегии."""
    from strategies import AVAILABLE_STRATEGIES
    return AVAILABLE_STRATEGIES


def get_available_instruments(exchange: str, interval: str) -> List[str]:
    """Сканирует папку с данными и возвращает список доступных инструментов."""
    data_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval)
    if not os.path.isdir(data_path):
        return []
    return sorted([f.replace('.parquet', '') for f in os.listdir(data_path) if f.endswith('.parquet')])


def run_update_lists():
    """Интерактивный запуск обновления списков ликвидных инструментов."""
    print("\n--- Обновление списков ликвидных инструментов ---\n")

    exchange = questionary.select(
        "Выберите биржу, для которой нужно обновить список:",
        choices=["tinkoff", "bybit", GO_BACK_OPTION],
        use_indicator=True
    ).ask()

    if exchange is None or exchange == GO_BACK_OPTION: return

    command = [sys.executable, "update_lists.py", "--exchange", exchange]
    print(f"\nЗапускаю обновление для {exchange.upper()}...\n")
    subprocess.run(command)


def run_download_data():
    """Интерактивный запуск скачивания данных."""
    print("\n--- Скачивание исторических данных ---\n")

    while True:
        download_mode = questionary.select(
            "Что вы хотите скачать?",
            choices=[
                "Отдельные тикеры (ручной ввод)",
                "Готовый список ликвидных инструментов",
                GO_BACK_OPTION
            ],
            use_indicator=True
        ).ask()

        if download_mode is None or download_mode == GO_BACK_OPTION: return

        exchange = questionary.select(
            "Выберите биржу:",
            choices=["tinkoff", "bybit", GO_BACK_OPTION],
            use_indicator=True
        ).ask()

        if exchange is None: return
        if exchange == GO_BACK_OPTION: continue

        command_args = []
        if "Отдельные тикеры" in download_mode:
            instruments_str = questionary.text(
                f"Введите тикеры для {exchange.upper()} через пробел (или оставьте пустым для возврата):"
            ).ask()
            if not instruments_str: continue
            command_args = ["--instrument", *instruments_str.split()]
        else:
            lists_dir = "datalists"
            if not os.path.isdir(lists_dir):
                print(f"Ошибка: Папка '{lists_dir}' не найдена. Создайте списки через соответствующий пункт меню.")
                return
            available_lists = sorted(
                [f for f in os.listdir(lists_dir) if f.startswith(exchange) and f.endswith('.txt')])
            if not available_lists:
                print(
                    f"Не найдено готовых списков для {exchange.upper()}. Создайте их через соответствующий пункт меню.")
                return

            selected_list = questionary.select(
                "Выберите список для скачивания:",
                choices=[*available_lists, GO_BACK_OPTION],
                use_indicator=True
            ).ask()
            if selected_list is None: return
            if selected_list == GO_BACK_OPTION: continue
            command_args = ["--list", selected_list]

        # Используем импортированный маппинг
        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
        interval = questionary.select(
            "Выберите интервал:",
            choices=[*available_intervals, GO_BACK_OPTION],
            use_indicator=True
        ).ask()
        if interval is None: return
        if interval == GO_BACK_OPTION: continue

        days = questionary.text(
            "Введите количество дней для загрузки (или оставьте пустым для возврата):",
            default="365",
            validate=lambda text: text.isdigit() and int(text) > 0 or text == "",
        ).ask()
        if not days: continue

        command = [
            sys.executable, "download_data.py", "--exchange", exchange,
            *command_args, "--interval", interval, "--days", days
        ]
        print("\nЗапускаю скачивание...\n")
        subprocess.run(command)
        break


def run_single_backtest():
    """Интерактивный запуск одиночного бэктеста."""
    print("\n--- Запуск одиночного бэктеста ---\n")

    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    while True:
        strategy_name = questionary.select("Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION]).ask()
        if strategy_name is None or strategy_name == GO_BACK_OPTION: return

        exchange = questionary.select("Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION]).ask()
        if exchange is None: return
        if exchange == GO_BACK_OPTION: continue

        # Используем импортированный маппинг
        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())

        strategy_class = strategies[strategy_name]
        default_params = strategy_class.get_default_params()
        default_interval = default_params.get("candle_interval")

        interval = questionary.select(
            "Выберите интервал:",
            choices=[*available_intervals, GO_BACK_OPTION],
            # Используем значение из конфига как default
            default=default_interval if default_interval in available_intervals else None
        ).ask()
        if interval is None: return
        if interval == GO_BACK_OPTION: continue

        available_instruments = get_available_instruments(exchange, interval)
        if not available_instruments:
            print(f"Ошибка: не найдено скачанных данных для биржи '{exchange}' и интервала '{interval}'.")
            print("Сначала скачайте данные с помощью соответствующего пункта меню.")
            continue

        instrument = questionary.select("Выберите инструмент:", choices=[*available_instruments, GO_BACK_OPTION]).ask()
        if instrument is None: return
        if instrument == GO_BACK_OPTION: continue

        rm_type = questionary.select("Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION],
                                     default="FIXED").ask()
        if rm_type is None: return
        if rm_type == GO_BACK_OPTION: continue

        command = [
            sys.executable, "run_backtest.py", "--strategy", strategy_name, "--exchange", exchange,
            "--instrument", instrument, "--interval", interval, "--rm", rm_type
        ]
        subprocess.run(command)
        break


def run_batch_backtest():
    """Интерактивный запуск массового бэктеста."""
    print("\n--- Запуск массового бэктеста ---\n")

    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    while True:
        strategy_name = questionary.select("Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION]).ask()
        if strategy_name is None or strategy_name == GO_BACK_OPTION: return

        exchange = questionary.select("Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION]).ask()
        if exchange is None: return
        if exchange == GO_BACK_OPTION: continue

        # Используем импортированный маппинг
        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())

        strategy_class = strategies[strategy_name]

        # Читаем рекомендуемый интервал из новой конфигурации класса стратегии
        default_params = strategy_class.get_default_params()
        default_interval = default_params.get("candle_interval")

        interval = questionary.select(
            "Выберите интервал:",
            choices=[*available_intervals, GO_BACK_OPTION],
            # Используем значение из новой конфигурации как default
            default=default_interval if default_interval in available_intervals else None
        ).ask()

        if interval is None: return
        if interval == GO_BACK_OPTION: continue

        rm_type = questionary.select("Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION],
                                     default="FIXED").ask()
        if rm_type is None: return
        if rm_type == GO_BACK_OPTION: continue

        command = [
            sys.executable, "batch_tester.py", "--strategy", strategy_name,
            "--exchange", exchange, "--interval", interval, "--rm", rm_type
        ]
        subprocess.run(command)
        break


def run_sandbox_trading():
    """Интерактивный запуск live-симуляции в 'песочнице'."""
    print("\n--- Запуск симуляции в 'песочнице' ---\n")

    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    while True:
        exchange = questionary.select("Выберите биржу:", choices=["bybit", "tinkoff", GO_BACK_OPTION]).ask()
        if exchange is None or exchange == GO_BACK_OPTION: return

        instrument = questionary.text(
            f"Введите тикер для {exchange.upper()} (или оставьте пустым для возврата):"
        ).ask()
        if not instrument: continue

        strategy_name = questionary.select("Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION]).ask()
        if strategy_name is None: return
        if strategy_name == GO_BACK_OPTION: continue

        live_intervals = ['1min', '3min', '5min', '15min']
        # Используем импортированный маппинг
        available_intervals = [i for i in live_intervals if i in EXCHANGE_INTERVAL_MAPS[exchange]]
        interval = questionary.select("Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION],
                                      default="1min").ask()
        if interval is None: return
        if interval == GO_BACK_OPTION: continue

        rm_type = questionary.select("Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION],
                                     default="FIXED").ask()
        if rm_type is None: return
        if rm_type == GO_BACK_OPTION: continue

        command = [
            sys.executable, "run_live.py", "--exchange", exchange, "--instrument", instrument,
            "--interval", interval, "--strategy", strategy_name, "--rm", rm_type
        ]

        if exchange == 'bybit':
            category = questionary.select(
                "Выберите категорию рынка Bybit:",
                choices=["linear", "spot", "inverse", GO_BACK_OPTION],
                default="linear"
            ).ask()
            if category is None: return
            if category == GO_BACK_OPTION: continue
            command.extend(["--category", category])

        print("\nЗапускаю live-бота... Нажмите Ctrl+C в этом окне, чтобы остановить.")
        subprocess.run(command)
        break


def run_optimizer():
    """Интерактивный запуск Walk-Forward Optimizer."""
    print("\n--- Запуск оптимизации параметров (Walk-Forward) ---\n")

    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    # --- Сбор параметров ---
    while True:
        strategy_name = questionary.select("Выберите стратегию для оптимизации:",
                                           choices=[*strategies.keys(), GO_BACK_OPTION]).ask()
        if strategy_name is None or strategy_name == GO_BACK_OPTION: return

        exchange = questionary.select("Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION]).ask()
        if exchange is None: return
        if exchange == GO_BACK_OPTION: continue

        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
        interval = questionary.select("Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION]).ask()
        if interval is None: return
        if interval == GO_BACK_OPTION: continue

        available_instruments = get_available_instruments(exchange, interval)
        if not available_instruments:
            print(f"Ошибка: не найдено скачанных данных для биржи '{exchange}' и интервала '{interval}'.")
            continue

        instrument = questionary.select("Выберите ОДИН инструмент для проведения WFO:",
                                        choices=[*available_instruments, GO_BACK_OPTION]).ask()
        if instrument is None: return
        if instrument == GO_BACK_OPTION: continue

        rm_type = questionary.select("Выберите риск-менеджер для оптимизации:",
                                     choices=["FIXED", "ATR", GO_BACK_OPTION]).ask()
        if rm_type is None: return
        if rm_type == GO_BACK_OPTION: continue

        n_trials = questionary.text(
            "Введите количество итераций на каждом шаге WFO (например, 100):",
            default="100", validate=lambda text: text.isdigit() and int(text) > 0
        ).ask()
        if n_trials is None: continue

        total_periods = questionary.text(
            "На сколько частей разделить всю историю (например, 10):",
            default="10", validate=lambda text: text.isdigit() and int(text) > 1
        ).ask()
        if total_periods is None: continue

        train_periods = questionary.text(
            f"Сколько частей использовать для обучения (1-{int(total_periods) - 1}):",
            default="5", validate=lambda text: text.isdigit() and 0 < int(text) < int(total_periods)
        ).ask()
        if train_periods is None: continue

        # Собираем команду для запуска
        command = [
            sys.executable, "-m", "optimization.runner",
            "--strategy", strategy_name,
            "--exchange", exchange,
            "--instrument", instrument,
            "--interval", interval,
            "--rm", rm_type,
            "--n_trials", n_trials,
            "--total_periods", total_periods,
            "--train_periods", train_periods,
        ]

        print("\nЗапускаю Walk-Forward Optimizer... Это может занять много времени.")
        print("Следите за прогрессом в этом окне. По завершении отчеты будут в папке optimization/reports/.")
        subprocess.run(command)
        break

def run_dashboard():
    """Запуск Streamlit дашборда."""
    print("\n--- Запуск панели анализа (Dashboard) ---\n")
    command = ["streamlit", "run", "dashboard.py"]
    print("Чтобы остановить дашборд, нажмите Ctrl+C в этом окне.")
    subprocess.run(command)


def main():
    """Отображает главное меню и вызывает соответствующий обработчик."""
    menu_actions = {
        "1. Обновить списки ликвидных инструментов": run_update_lists,
        "2. Скачать исторические данные": run_download_data,
        "-----------------------------------------": None,
        "3. Запустить бэктест на одном инструменте": run_single_backtest,
        "4. Запустить массовый бэктест (по папке)": run_batch_backtest,
        "5. Проанализировать результаты (Dashboard)": run_dashboard,
        "6. Запустить оптимизацию параметров (WFO)": run_optimizer,
        "------------------------------------------": None,
        "7. Запустить симуляцию в 'песочнице'": run_sandbox_trading,
        "Выход": "EXIT"
    }

    while True:
        pretty_choices = []
        for key in menu_actions.keys():
            if menu_actions[key] is None:
                pretty_choices.append(questionary.Separator(key))
            else:
                pretty_choices.append(key)

        choice = questionary.select(
            "Главное меню:",
            choices=pretty_choices,
            use_indicator=True
        ).ask()

        if choice is None or choice == "Выход":
            print("Завершение работы.")
            break

        action = menu_actions.get(choice)
        if action:
            try:
                action()
            except Exception as e:
                print(f"\nПроизошла ошибка: {e}\n")


if __name__ == "__main__":
    main()