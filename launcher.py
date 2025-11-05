# launcher.py

import questionary
import subprocess
import sys
import os
from typing import List, Dict, Type
from questionary import ValidationError, Validator

from strategies.base_strategy import BaseStrategy
from config import PATH_CONFIG
from utils.data_clients import EXCHANGE_INTERVAL_MAPS
from update_lists import LIST_UPDATERS


class NumberValidator(Validator):
    """Проверяет, что введенное значение является положительным числом."""

    def validate(self, document):
        try:
            value = int(document.text)
            if value <= 0:
                raise ValidationError(
                    message="Пожалуйста, введите положительное число (больше нуля).",
                    cursor_position=len(document.text))
        except ValueError:
            raise ValidationError(
                message="Пожалуйста, введите целое число.",
                cursor_position=len(document.text))


def get_available_strategies() -> Dict[str, Type[BaseStrategy]]:
    from strategies import AVAILABLE_STRATEGIES
    return AVAILABLE_STRATEGIES


def get_available_instruments(exchange: str, interval: str) -> List[str]:
    data_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval)
    if not os.path.isdir(data_path):
        return []
    return [f.replace('.parquet', '') for f in os.listdir(data_path) if f.endswith('.parquet')]


def run_update_lists():
    print("\n--- Обновление списков инструментов ---\n")
    list_type_choices = {"top50_liquid": "Топ-50 самых ликвидных инструментов"}
    selected_type_key = questionary.select("Какой тип списка вы хотите обновить?",
                                           choices=list(list_type_choices.values()), use_indicator=True).ask()
    if selected_type_key is None: return
    list_type = next(key for key, value in list_type_choices.items() if value == selected_type_key)
    exchange = questionary.select("Выберите биржу:", choices=["tinkoff", "bybit"], use_indicator=True).ask()
    if exchange is None: return
    command = [sys.executable, "update_lists.py", "--exchange", exchange, "--list-type", list_type]
    print(f"\nЗапускаю обновление списка '{list_type}' для {exchange.upper()}...\n")
    subprocess.run(command)


def run_download_data():
    print("\n--- Скачивание исторических данных ---\n")
    download_mode = questionary.select("Что вы хотите скачать?",
                                       choices=["Отдельные тикеры (ручной ввод)", "Готовый список инструментов"],
                                       use_indicator=True).ask()
    if download_mode is None: return
    exchange = questionary.select("Выберите биржу:", choices=["tinkoff", "bybit"], use_indicator=True).ask()
    if exchange is None: return
    command_args = []
    if "Отдельные тикеры" in download_mode:
        instruments_str = questionary.text(
            f"Введите тикеры для {exchange.upper()} через пробел (например, {'SBER GAZP' if exchange == 'tinkoff' else 'BTCUSDT ETHUSDT'}):").ask()
        if not instruments_str:
            print("Ошибка: не введено ни одного тикера.")
            return
        command_args = ["--instrument", *instruments_str.split()]
    else:
        lists_dir = "datalists"
        if not os.path.isdir(lists_dir) or not os.listdir(lists_dir):
            print(f"Ошибка: Папка '{lists_dir}' пуста или не найдена.")
            print("Сначала создайте списки с помощью первого пункта меню 'Обновить списки инструментов'.")
            return
        available_lists = [f for f in os.listdir(lists_dir) if f.startswith(exchange) and f.endswith('.txt')]
        if not available_lists:
            print(f"Не найдено готовых списков для {exchange.upper()}.")
            print(f"Сначала запустите 'Обновить списки инструментов' для создания.")
            return
        selected_list = questionary.select("Выберите список для скачивания:", choices=available_lists,
                                           use_indicator=True).ask()
        if selected_list is None: return
        command_args = ["--list", selected_list]
    available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
    interval = questionary.select("Выберите интервал:", choices=available_intervals, use_indicator=True).ask()
    if interval is None: return
    days = questionary.text("Введите количество дней для загрузки:", default="365", validate=NumberValidator).ask()
    if days is None: return
    command = [sys.executable, "download_data.py", "--exchange", exchange, *command_args, "--interval", interval,
               "--days", days]
    if exchange == 'bybit':
        category = questionary.select("Выберите категорию рынка Bybit:", choices=["linear", "spot", "inverse"],
                                      default="linear").ask()
        if category is None: return
        command.extend(["--category", category])
    print("\nЗапускаю скачивание...\n")
    subprocess.run(command)


def run_single_backtest():
    print("\n--- Запуск одиночного бэктеста ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии в run_backtest.py.")
        return
    strategy_name = questionary.select("Выберите стратегию:", choices=list(strategies.keys())).ask()
    if strategy_name is None: return
    exchange = questionary.select("Выберите биржу для бэктеста:", choices=["tinkoff", "bybit"]).ask()
    if exchange is None: return
    available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
    interval = questionary.select("Выберите интервал:", choices=available_intervals,
                                  default=strategies[strategy_name].candle_interval if strategies[
                                                                                           strategy_name].candle_interval in available_intervals else None).ask()
    if interval is None: return
    available_instruments = get_available_instruments(exchange, interval)
    if not available_instruments:
        print(f"Ошибка: не найдено скачанных данных для интервала '{interval}'.")
        print("Сначала скачайте данные с помощью первого пункта меню.")
        return
    instrument = questionary.select("Выберите инструмент:", choices=available_instruments).ask()
    if instrument is None: return
    rm_type = questionary.select("Выберите риск-менеджер:", choices=["FIXED", "ATR"], default="FIXED").ask()
    if rm_type is None: return
    command = [sys.executable, "run_backtest.py", "--strategy", strategy_name, "--exchange", exchange, "--instrument",
               instrument, "--interval", interval, "--rm", rm_type]
    subprocess.run(command)


def run_batch_backtest():
    print("\n--- Запуск массового бэктеста ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии в run_backtest.py.")
        return
    strategy_name = questionary.select("Выберите стратегию:", choices=list(strategies.keys())).ask()
    if strategy_name is None: return
    exchange = questionary.select("Выберите биржу для массового теста:", choices=["tinkoff", "bybit"]).ask()
    if exchange is None: return
    available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
    interval = questionary.select("Выберите интервал:", choices=available_intervals,
                                  default=strategies[strategy_name].candle_interval if strategies[
                                                                                           strategy_name].candle_interval in available_intervals else None).ask()
    if interval is None: return
    rm_type = questionary.select("Выберите риск-менеджер:", choices=["FIXED", "ATR"], default="FIXED").ask()
    if rm_type is None: return
    command = [sys.executable, "batch_tester.py", "--strategy", strategy_name, "--exchange", exchange, "--interval",
               interval, "--rm", rm_type]
    subprocess.run(command)


def run_sandbox_trading():
    print("\n--- Запуск симуляции в 'песочнице' ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии в run_backtest.py.")
        return
    exchange = questionary.select("Выберите биржу:", choices=["bybit", "tinkoff"]).ask()
    if exchange is None: return
    instrument = questionary.text(
        f"Введите тикер инструмента для {exchange.upper()} (например, {'BTCUSDT' if exchange == 'bybit' else 'SBER'}):").ask()
    if not instrument: return
    strategy_name = questionary.select("Выберите стратегию:", choices=list(strategies.keys())).ask()
    if strategy_name is None: return
    live_intervals = ['1min', '3min', '5min', '15min']
    available_intervals = [i for i in live_intervals if i in EXCHANGE_INTERVAL_MAPS[exchange]]
    interval = questionary.select("Выберите интервал:", choices=available_intervals,
                                  default="1min" if "1min" in available_intervals else None).ask()
    if interval is None: return
    rm_type = questionary.select("Выберите риск-менеджер:", choices=["FIXED", "ATR"], default="FIXED").ask()
    if rm_type is None: return
    command = [sys.executable, "run_live.py", "--exchange", exchange, "--instrument", instrument, "--interval",
               interval, "--strategy", strategy_name, "--rm", rm_type]
    if exchange == 'bybit':
        category = questionary.select("Выберите категорию рынка Bybit:", choices=["linear", "spot", "inverse"],
                                      default="linear").ask()
        if category is None: return
        command.extend(["--category", category])
    print("\nЗапускаю live-бота... Нажмите Ctrl+C в этом окне, чтобы остановить.")
    subprocess.run(command)


def run_dashboard():
    print("\n--- Запуск панели анализа (Dashboard) ---\n")
    command = ["streamlit", "run", "dashboard.py"]
    print("Чтобы остановить дашборд, нажмите Ctrl+C в этом окне.")
    subprocess.run(command)


def main():
    """Отображает главное меню и вызывает соответствующий обработчик."""
    menu_actions = {
        "1. Обновить списки инструментов (Топ-50 ликвидных и др.)": run_update_lists,
        "2. Скачать исторические данные": run_download_data,
        "-----------------------------------------": None,
        "3. Запустить бэктест на одном инструменте": run_single_backtest,
        "4. Запустить массовый бэктест (по папке)": run_batch_backtest,
        "5. Проанализировать результаты (Dashboard)": run_dashboard,
        "------------------------------------------": None,
        "6. Запустить симуляцию в 'песочнице'": run_sandbox_trading,
        "Выход": "EXIT_APP"
    }

    while True:
        pretty_choices = []
        # Используем .items() для доступа и к ключу, и к значению
        for key, value in menu_actions.items():
            if value is None:
                # Это по-прежнему обрабатывает только "---"
                pretty_choices.append(questionary.Separator(key))
            else:
                # А это теперь добавляет все остальные пункты, включая "Выход"
                pretty_choices.append(key)

        choice = questionary.select("Главное меню:", choices=pretty_choices, use_indicator=True).ask()


        if choice is None or choice == "Выход":
            print("Завершение работы.")
            break

        action = menu_actions.get(choice)

        if callable(action):
            try:
                action()
            except Exception as e:
                print(f"\nПроизошла ошибка: {e}\n")

            questionary.press_any_key_to_continue().ask()


if __name__ == "__main__":
    main()