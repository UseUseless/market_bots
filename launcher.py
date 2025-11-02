import questionary
import subprocess
import sys
import os
from typing import List, Dict, Type

from strategies.base_strategy import BaseStrategy
from config import PATH_CONFIG


# --- Динамический импорт и сбор доступных опций ---

def get_available_strategies() -> Dict[str, Type[BaseStrategy]]:
    """Динамически находит и импортирует все доступные стратегии."""
    # Это позволяет не обновлять список вручную.
    # Просто добавь новую стратегию в run.py, и она появится здесь.
    from run import AVAILABLE_STRATEGIES
    return AVAILABLE_STRATEGIES


def get_available_instruments(interval: str) -> List[str]:
    """Сканирует папку с данными и возвращает список доступных инструментов."""
    data_path = os.path.join(PATH_CONFIG["DATA_DIR"], interval)
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
        "Введите тикеры через пробел (например, SBER GAZP или BTCUSDT ETHUSDT):"
    ).ask()

    interval = questionary.text(
        "Введите интервал (например, 5min, 1hour, 1day):",
        default="5min"
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
        print("Ошибка: не найдено ни одной доступной стратегии в run.py.")
        return

    strategy_name = questionary.select(
        "Выберите стратегию:",
        choices=list(strategies.keys())
    ).ask()

    # Сначала спрашиваем интервал, чтобы найти доступные инструменты
    interval = questionary.text(
        "Введите интервал для бэктеста:",
        default=strategies[strategy_name].candle_interval
    ).ask()

    available_instruments = get_available_instruments(interval)
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
        sys.executable, "run.py",
        "--strategy", strategy_name,
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
        print("Ошибка: не найдено ни одной доступной стратегии в run.py.")
        return

    strategy_name = questionary.select(
        "Выберите стратегию:",
        choices=list(strategies.keys())
    ).ask()

    interval = questionary.text(
        "Введите интервал для массового теста:",
        default=strategies[strategy_name].candle_interval
    ).ask()

    rm_type = questionary.select(
        "Выберите риск-менеджер:",
        choices=["FIXED", "ATR"],
        default="FIXED"
    ).ask()

    command = [
        sys.executable, "batch_tester.py",
        "--strategy", strategy_name,
        "--interval", interval,
        "--rm", rm_type
    ]

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
        "Проанализировать результаты (Dashboard)": run_dashboard,
        # "Запустить симуляцию в 'песочнице'": None, # Заготовка на будущее
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