# launcher.py

import questionary
import subprocess
import sys
import os
from typing import List, Dict, Type

from strategies.base_strategy import BaseStrategy
from config import PATH_CONFIG, EXCHANGE_INTERVAL_MAPS
from optimization.metrics import METRIC_CONFIG

# Кастомное исключение для выхода из цепочки вопросов.
class UserCancelledError(Exception):
    """Используется, когда пользователь отменяет ввод (нажимает Ctrl+C или 'Назад')."""
    pass

# 2. Константа для опции "Назад".
GO_BACK_OPTION = "Назад"

# 3. Helper-функция (обертка) для всех вызовов questionary.
def ask(question_func, *args, **kwargs):
    """
    Оборачивает вызов функции из questionary, проверяет результат на отмену
    и выбрасывает UserCancelledError, если пользователь решил выйти.
    """
    # .ask() возвращает None при нажатии Ctrl+C
    answer = question_func(*args, **kwargs).ask()
    if answer is None or answer == GO_BACK_OPTION:
        raise UserCancelledError()
    return answer

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
    try:
        exchange = ask(
            questionary.select,
            "Выберите биржу, для которой нужно обновить список:",
            choices=["tinkoff", "bybit", GO_BACK_OPTION],
            use_indicator=True
        )
        command = [sys.executable, "update_lists.py", "--exchange", exchange]
        print(f"\nЗапускаю обновление для {exchange.upper()}...\n")
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


def run_download_data():
    """Интерактивный запуск скачивания данных."""
    print("\n--- Скачивание исторических данных ---\n")
    try:
        download_mode = ask(
            questionary.select,
            "Что вы хотите скачать?",
            choices=["Отдельные тикеры (ручной ввод)", "Готовый список ликвидных инструментов", GO_BACK_OPTION],
            use_indicator=True
        )
        exchange = ask(questionary.select, "Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION], use_indicator=True)

        command_args = []
        if "Отдельные тикеры" in download_mode:
            instruments_str = ask(questionary.text, f"Введите тикеры для {exchange.upper()} через пробел:")
            command_args = ["--instrument", *instruments_str.split()]
        else:
            lists_dir = "datalists"
            if not os.path.isdir(lists_dir):
                print(f"Ошибка: Папка '{lists_dir}' не найдена. Создайте списки через соответствующий пункт меню.")
                return
            available_lists = sorted([f for f in os.listdir(lists_dir) if f.startswith(exchange) and f.endswith('.txt')])
            if not available_lists:
                print(f"Не найдено готовых списков для {exchange.upper()}. Создайте их через соответствующий пункт меню.")
                return
            selected_list = ask(questionary.select, "Выберите список для скачивания:", choices=[*available_lists, GO_BACK_OPTION], use_indicator=True)
            command_args = ["--list", selected_list]

        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION], use_indicator=True)
        days = ask(questionary.text, "Введите количество дней для загрузки:", default="365", validate=lambda text: text.isdigit() and int(text) > 0)

        command = [sys.executable, "download_data.py", "--exchange", exchange, *command_args, "--interval", interval, "--days", days]
        print("\nЗапускаю скачивание...\n")
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


def run_single_backtest():
    """Интерактивный запуск одиночного бэктеста."""
    print("\n--- Запуск одиночного бэктеста ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    try:
        strategy_name = ask(questionary.select, "Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION])
        exchange = ask(questionary.select, "Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION])

        strategy_class = strategies[strategy_name]
        default_interval = strategy_class.get_default_params().get("candle_interval")
        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION], default=default_interval if default_interval in available_intervals else None)

        available_instruments = get_available_instruments(exchange, interval)
        if not available_instruments:
            print(f"\nОшибка: не найдено скачанных данных для биржи '{exchange}' и интервала '{interval}'.")
            print("Сначала скачайте данные с помощью соответствующего пункта меню.")
            return

        instrument = ask(questionary.select, "Выберите инструмент:", choices=[*available_instruments, GO_BACK_OPTION])
        rm_type = ask(questionary.select, "Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION], default="FIXED")

        command = [sys.executable, "run_backtest.py", "--strategy", strategy_name, "--exchange", exchange, "--instrument", instrument, "--interval", interval, "--rm", rm_type]
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


def run_batch_backtest():
    """Интерактивный запуск массового бэктеста."""
    print("\n--- Запуск массового бэктеста ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    try:
        strategy_name = ask(questionary.select, "Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION])
        exchange = ask(questionary.select, "Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION])

        strategy_class = strategies[strategy_name]
        default_interval = strategy_class.get_default_params().get("candle_interval")
        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION], default=default_interval if default_interval in available_intervals else None)

        rm_type = ask(questionary.select, "Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION], default="FIXED")

        command = [sys.executable, "batch_tester.py", "--strategy", strategy_name, "--exchange", exchange, "--interval", interval, "--rm", rm_type]
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


def run_sandbox_trading():
    """Интерактивный запуск live-симуляции в 'песочнице'."""
    print("\n--- Запуск симуляции в 'песочнице' ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    try:
        exchange = ask(questionary.select, "Выберите биржу:", choices=["bybit", "tinkoff", GO_BACK_OPTION])
        instrument = ask(questionary.text, f"Введите тикер для {exchange.upper()}:")
        strategy_name = ask(questionary.select, "Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION])

        live_intervals = ['1min', '3min', '5min', '15min']
        available_intervals = [i for i in live_intervals if i in EXCHANGE_INTERVAL_MAPS[exchange]]
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION], default="1min")
        rm_type = ask(questionary.select, "Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION], default="FIXED")

        command = [sys.executable, "run_live.py", "--exchange", exchange, "--instrument", instrument, "--interval", interval, "--strategy", strategy_name, "--rm", rm_type]

        if exchange == 'bybit':
            category = ask(questionary.select, "Выберите категорию рынка Bybit:", choices=["linear", "spot", "inverse", GO_BACK_OPTION], default="linear")
            command.extend(["--category", category])

        print("\nЗапускаю live-бота... Нажмите Ctrl+C в этом окне, чтобы остановить.")
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


def run_optimizer():
    """Интерактивный запуск Walk-Forward Optimizer."""
    print("\n--- Запуск оптимизации параметров (Walk-Forward) ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    try:
        strategy_name = ask(questionary.select, "Выберите стратегию для оптимизации:", choices=[*strategies.keys(), GO_BACK_OPTION])
        exchange = ask(questionary.select, "Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION])
        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION])

        available_instruments = get_available_instruments(exchange, interval)
        if not available_instruments:
            print(f"\nОшибка: не найдено скачанных данных для биржи '{exchange}' и интервала '{interval}'.")
            return

        instrument = ask(questionary.select, "Выберите ОДИН инструмент для проведения WFO:", choices=[*available_instruments, GO_BACK_OPTION])
        rm_type = ask(questionary.select, "Выберите риск-менеджер для оптимизации:", choices=["FIXED", "ATR", GO_BACK_OPTION])

        metric_choices = [questionary.Choice(title=f"{v['name']} ({v['direction']})", value=k, description=v['description']) for k, v in METRIC_CONFIG.items()]
        default_metric = next((c for c in metric_choices if c.value == "calmar_ratio"), None)
        selected_metric = ask(questionary.select, "Выберите целевую метрику для оптимизации:", choices=metric_choices, use_indicator=True, default=default_metric)

        n_trials = ask(questionary.text, "Введите количество итераций на каждом шаге WFO (например, 100):", default="100", validate=lambda text: text.isdigit() and int(text) > 0)
        total_periods = ask(questionary.text, "На сколько частей разделить всю историю (например, 10):", default="10", validate=lambda text: text.isdigit() and int(text) > 1)
        train_periods = ask(questionary.text, f"Сколько частей использовать для обучения (1-{int(total_periods) - 1}):", default="5", validate=lambda text: text.isdigit() and 0 < int(text) < int(total_periods))

        command = [sys.executable, "-m", "optimization.runner", "--strategy", strategy_name, "--exchange", exchange, "--instrument", instrument, "--interval", interval, "--rm", rm_type, "--metric", selected_metric, "--n_trials", n_trials, "--total_periods", total_periods, "--train_periods", train_periods]

        print("\nЗапускаю Walk-Forward Optimizer... Это может занять много времени.")
        print("Следите за прогрессом в этом окне. По завершении отчеты будут в папке optimization/reports/.")
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


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
        # Формируем красивый список для главного меню
        pretty_choices = [questionary.Separator(key) if action is None else key for key, action in menu_actions.items()]
        choice = questionary.select("Главное меню:", choices=pretty_choices, use_indicator=True).ask()

        if choice is None or choice == "Выход":
            print("Завершение работы.")
            break

        action = menu_actions.get(choice)
        if action:
            try:
                action()
                # Добавим небольшую паузу и просьбу нажать Enter для возврата в меню
                input("\nНажмите Enter, чтобы вернуться в главное меню...")
            except Exception as e:
                print(f"\nПроизошла критическая ошибка: {e}\n")
                input("\nНажмите Enter, чтобы вернуться в главное меню...")

if __name__ == "__main__":
    main()