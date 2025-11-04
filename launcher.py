import questionary
import subprocess
import sys
import os
from typing import List, Dict, Type

from strategies.base_strategy import BaseStrategy
from config import PATH_CONFIG
from utils.data_clients import EXCHANGE_INTERVAL_MAPS

# --- Динамический импорт и сбор доступных опций ---

def get_available_strategies() -> Dict[str, Type[BaseStrategy]]:
    """Динамически находит и импортирует все доступные стратегии."""
    # Это позволяет не обновлять список вручную.
    from strategies import AVAILABLE_STRATEGIES
    return AVAILABLE_STRATEGIES


def get_available_instruments(exchange: str, interval: str) -> List[str]:
    """Сканирует папку с данными и возвращает список доступных инструментов."""
    data_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval)
    if not os.path.isdir(data_path):
        return []
    return [f.replace('.parquet', '') for f in os.listdir(data_path) if f.endswith('.parquet')]


# --- Функции-обработчики для каждого пункта меню ---

def run_download_data():
    """Интерактивный запуск скачивания данных."""
    print("\n--- Скачивание исторических данных ---\n")

    exchange = questionary.select(
        "Выберите биржу:",
        choices=["tinkoff", "bybit"],
    ).ask()

    instruments = questionary.text(
        f"Введите тикеры для {exchange.upper()} через пробел (например, SBER GAZP или BTCUSDT ETHUSDT):"
    ).ask()

    available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
    interval = questionary.select(
        "Выберите интервал:",
        choices=available_intervals
    ).ask()

    days = questionary.text(
        "Введите количество дней для загрузки:",
        default="365"
    ).ask()

    command = [
        sys.executable, "download_data.py",
        "--exchange", exchange,
        "--instrument", *instruments.split(),
        "--interval", interval,
        "--days", days
    ]

    subprocess.run(command)


def run_single_backtest():
    """Интерактивный запуск одиночного бэктеста."""
    print("\n--- Запуск одиночного бэктеста ---\n")

    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии в run_backtest.py.")
        return

    strategy_name = questionary.select(
        "Выберите стратегию:",
        choices=list(strategies.keys())
    ).ask()

    exchange = questionary.select(
        "Выберите биржу для бэктеста:",
        choices=["tinkoff", "bybit"],
    ).ask()

    # Сначала спрашиваем интервал, чтобы найти доступные инструменты
    available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
    interval = questionary.select(
        "Выберите интервал:",
        choices=available_intervals,
        default=strategies[strategy_name].candle_interval if strategies[strategy_name].candle_interval in available_intervals else None
    ).ask()

    available_instruments = get_available_instruments(exchange, interval)
    if not available_instruments:
        print(f"Ошибка: не найдено скачанных данных для интервала '{interval}'.")
        print("Сначала скачайте данные с помощью первого пункта меню.")
        return

    instrument = questionary.select(
        "Выберите инструмент:",
        choices=available_instruments
    ).ask()

    rm_type = questionary.select(
        "Выберите риск-менеджер:",
        choices=["FIXED", "ATR"],
        default="FIXED"
    ).ask()

    command = [
        sys.executable, "run_backtest.py",
        "--strategy", strategy_name,
        "--exchange", exchange,
        "--instrument", instrument,
        "--interval", interval,
        "--rm", rm_type
    ]

    subprocess.run(command)


def run_batch_backtest():
    """Интерактивный запуск массового бэктеста."""
    print("\n--- Запуск массового бэктеста ---\n")

    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии в run_backtest.py.")
        return

    strategy_name = questionary.select(
        "Выберите стратегию:",
        choices=list(strategies.keys())
    ).ask()

    exchange = questionary.select(
        "Выберите биржу для массового теста:",
        choices=["tinkoff", "bybit"],
    ).ask()

    available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
    interval = questionary.select(
        "Выберите интервал:",
        choices=available_intervals,
        default=strategies[strategy_name].candle_interval if strategies[strategy_name].candle_interval in available_intervals else None
    ).ask()

    rm_type = questionary.select(
        "Выберите риск-менеджер:",
        choices=["FIXED", "ATR"],
        default="FIXED"
    ).ask()

    command = [
        sys.executable, "batch_tester.py",
        "--strategy", strategy_name,
        "--exchange", exchange,
        "--interval", interval,
        "--rm", rm_type
    ]

    subprocess.run(command)


def run_sandbox_trading():
    """Интерактивный запуск live-симуляции в 'песочнице'."""
    print("\n--- Запуск симуляции в 'песочнице' ---\n")

    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии в run_backtest.py.")
        return

    exchange = questionary.select(
        "Выберите биржу:",
        choices=["bybit", "tinkoff"],
    ).ask()

    instrument = questionary.text(
        f"Введите тикер инструмента для {exchange.upper()} (например, {'BTCUSDT' if exchange == 'bybit' else 'SBER'}):"
    ).ask()

    strategy_name = questionary.select(
        "Выберите стратегию:",
        choices=list(strategies.keys())
    ).ask()

    live_intervals = ['1min', '3min', '5min', '15min']
    available_intervals = [i for i in live_intervals if i in EXCHANGE_INTERVAL_MAPS[exchange]]

    interval = questionary.select(
        "Выберите интервал:",
        choices=available_intervals,
        default="1min" if "1min" in available_intervals else None
    ).ask()


    rm_type = questionary.select(
        "Выберите риск-менеджер:",
        choices=["FIXED", "ATR"],
        default="FIXED"
    ).ask()

    command = [
        sys.executable, "run_live.py",
        "--exchange", exchange,
        "--instrument", instrument,
        "--interval", interval,
        "--strategy", strategy_name,
        "--rm", rm_type
    ]

    # Для Bybit нужно передать 'category'
    if exchange == 'bybit':
        category = questionary.select(
            "Выберите категорию рынка Bybit:",
            choices=["linear", "spot", "inverse"],
            default="linear"
        ).ask()
        command.extend(["--category", category])

    print("\nЗапускаю live-бота... Нажмите Ctrl+C в этом окне, чтобы остановить.")
    subprocess.run(command)


def run_dashboard():
    """Запуск Streamlit дашборда."""
    print("\n--- Запуск панели анализа (Dashboard) ---\n")
    command = ["streamlit", "run", "dashboard.py"]
    print("Чтобы остановить дашборд, нажмите Ctrl+C в этом окне.")
    subprocess.run(command)


# --- Главная функция ---

def main():
    """Отображает главное меню и вызывает соответствующий обработчик."""

    # Словарь, связывающий выбор в меню с функцией-обработчиком
    menu_actions = {
        "Скачать исторические данные": run_download_data,
        "Запустить бэктест на одном инструменте": run_single_backtest,
        "Запустить массовый бэктест": run_batch_backtest,
        "Запустить симуляцию в 'песочнице'": run_sandbox_trading,
        "Проанализировать результаты (Dashboard)": run_dashboard,
        "Выход": None
    }

    while True:
        choice = questionary.select(
            "Что вы хотите сделать?",
            choices=list(menu_actions.keys()),
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

        # Пауза перед возвращением в главное меню
        questionary.press_any_key_to_continue().ask()


if __name__ == "__main__":
    main()