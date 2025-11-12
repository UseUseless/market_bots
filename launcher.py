import questionary
import subprocess
import sys
import os
import json
from typing import List, Dict, Type, Any

from app.strategies.base_strategy import BaseStrategy
from app.core.risk_manager import AVAILABLE_RISK_MANAGERS
from config import PATH_CONFIG, EXCHANGE_INTERVAL_MAPS
from app.analyzers.metrics import METRIC_CONFIG
from app.ui import dialogs as ui_helpers


# Кастомное исключение для выхода из цепочки вопросов.
class UserCancelledError(Exception):
    """Используется, когда пользователь отменяет ввод (нажимает Ctrl+C или 'Назад')."""
    pass


# Константа для опции "Назад".
GO_BACK_OPTION = "Назад"


# Helper-функция (обертка) для всех вызовов questionary.
def ask(question_func, *args, **kwargs):
    """
    Оборачивает вызов функции из questionary, проверяет результат на отмену
    и выбрасывает UserCancelledError, если пользователь решил выйти.
    """
    answer = question_func(*args, **kwargs).ask()
    if answer is None or answer == GO_BACK_OPTION:
        raise UserCancelledError()
    return answer


def get_available_strategies() -> Dict[str, Type[BaseStrategy]]:
    """Динамически находит и импортирует все доступные стратегии."""
    from app.strategies import AVAILABLE_STRATEGIES
    return AVAILABLE_STRATEGIES


def _select_metrics_for_optimization() -> List[str]:
    """
    Инкапсулирует логику выбора одной или двух метрик для оптимизации.
    """
    mode = ask(
        questionary.select,
        "Выберите режим оптимизации:",
        choices=["Один критерий", "Два критерия (фронт Парето)", GO_BACK_OPTION],
        use_indicator=True
    )
    metric_choices = [
        questionary.Choice(
            title=f"{v['name']} ({v['direction']})",
            value=k,
            description=v['description']
        ) for k, v in METRIC_CONFIG.items()
    ]
    default_metric_choice = next((c for c in metric_choices if c.value == "calmar_ratio"), None)
    if "Один критерий" in mode:
        selected_metric = ask(
            questionary.select, "Выберите целевую метрику:",
            choices=metric_choices, use_indicator=True, default=default_metric_choice
        )
        return [selected_metric]
    else:
        first_metric = ask(
            questionary.select, "Выберите первую метрику (Цель №1):",
            choices=metric_choices, use_indicator=True, default=default_metric_choice
        )
        second_metric_choices = [c for c in metric_choices if c.value != first_metric]
        default_second_metric_choice = next((c for c in second_metric_choices if c.value == "max_drawdown"), None)
        second_metric = ask(
            questionary.select, "Выберите вторую метрику (Цель №2):",
            choices=second_metric_choices, use_indicator=True, default=default_second_metric_choice
        )
        return [first_metric, second_metric]

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
        command = [sys.executable, os.path.join("scripts", "update_lists.py"), "--exchange", exchange]
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
        exchange = ask(questionary.select, "Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION],
                       use_indicator=True)
        command_args = []
        if "Отдельные тикеры" in download_mode:
            instruments_str = ask(questionary.text, f"Введите тикеры для {exchange.upper()} через пробел:")
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
            selected_list = ask(questionary.select, "Выберите список для скачивания:",
                                choices=[*available_lists, GO_BACK_OPTION], use_indicator=True)
            command_args = ["--list", selected_list]
        available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION],
                       use_indicator=True)
        days = ask(questionary.text, "Введите количество дней для загрузки:", default="365",
                   validate=lambda text: text.isdigit() and int(text) > 0)
        command = [sys.executable, os.path.join("scripts", "download_data.py"), "--exchange", exchange, *command_args, "--interval", interval, "--days", days]
        print("\nЗапускаю скачивание...\n")
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


def _prompt_for_params(config: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """
    Интерактивно запрашивает у пользователя значения для оптимизируемых параметров.
    """
    custom_params = {}
    for name, p_config in config.items():
        if not p_config.get("optimizable", False):
            continue

        default_val = p_config["default"]
        description = p_config.get("description", "")
        param_type = p_config.get("type", "str")

        prompt_text = f"Параметр '{name}' ({description})"
        if "low" in p_config and "high" in p_config:
            prompt_text += f" [диапазон: {p_config['low']}-{p_config['high']}]"
        prompt_text += ". Нажмите Enter для значения по умолчанию:"

        while True:
            try:
                answer = ask(
                    questionary.text,
                    prompt_text,
                    default=str(default_val)
                )

                if param_type == "int":
                    custom_params[prefix + name] = int(answer)
                elif param_type == "float":
                    custom_params[prefix + name] = float(answer)
                else:
                    custom_params[prefix + name] = answer
                break
            except ValueError:
                print(f"Ошибка: Неверный тип данных. Ожидается {param_type}. Попробуйте снова.")
            except UserCancelledError:
                raise

    return custom_params

def run_backtest_flow():
    """Интерактивный воркфлоу для запуска бэктеста."""
    print("\n--- Запуск бэктеста ---\n")
    strategies = get_available_strategies()
    if not strategies:
        print("Ошибка: не найдено ни одной доступной стратегии.")
        return

    try:
        test_mode = ask(
            questionary.select, "Выберите режим запуска:",
            choices=["Один инструмент (выбор файла)", "Несколько инструментов (выбор папки)", GO_BACK_OPTION]
        )
        data_params = None
        command = []
        if "Один инструмент" in test_mode:
            data_params = ui_helpers.select_single_instrument()
            if data_params: command = [sys.executable, os.path.join("scripts", "run_backtest.py")]
        else:
            data_params = ui_helpers.select_instrument_folder()
            if data_params: command = [sys.executable, os.path.join("scripts", "batch_tester.py")]
        if not data_params: return

        strategy_name = ask(questionary.select, "Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION])
        rm_type = ask(questionary.select, "Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION],
                      default="FIXED")

        final_params = {}
        change_params = ask(
            questionary.confirm,
            "Хотите изменить параметры по умолчанию для этого запуска?",
            default=False
        )
        if change_params:
            strategy_class = strategies[strategy_name]
            rm_class = AVAILABLE_RISK_MANAGERS[rm_type]

            print("\n--- Настройка параметров стратегии ---")
            strat_params = _prompt_for_params(strategy_class.params_config)
            final_params.update(strat_params)

            print("\n--- Настройка параметров риск-менеджера ---")
            rm_params = _prompt_for_params(rm_class.params_config, prefix="rm_")
            final_params.update(rm_params)

        command.extend([
            "--strategy", strategy_name,
            "--exchange", data_params["exchange"],
            "--interval", data_params["interval"],
            "--rm", rm_type
        ])

        if "instrument" in data_params:
            command.append(f"--instrument={data_params['instrument']}")

        if final_params:
            command.append(f"--params={json.dumps(final_params)}")

        print("\nЗапускаю бэктест...")
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
        strategy_name = ask(questionary.select, "Выберите стратегию для оптимизации:",
                            choices=[*strategies.keys(), GO_BACK_OPTION])
        rm_type = ask(questionary.select, "Выберите риск-менеджер для оптимизации:",
                      choices=["FIXED", "ATR", GO_BACK_OPTION])
        opt_mode = ask(
            questionary.select,
            "Выберите объект оптимизации:",
            choices=["Один инструмент (выбор файла)", "Портфель инструментов (выбор папки)", GO_BACK_OPTION],
            use_indicator=True
        )
        command = [sys.executable, "-m", "optimization.runner"]
        data_params = None
        if "Один инструмент" in opt_mode:
            data_params = ui_helpers.select_single_instrument()
            if data_params:
                command.extend([
                    f"--exchange={data_params['exchange']}",
                    f"--interval={data_params['interval']}",
                    f"--instrument={data_params['instrument']}"
                ])
        else:
            data_params = ui_helpers.select_instrument_folder()
            if data_params:
                full_path = os.path.join(PATH_CONFIG["DATA_DIR"], data_params['exchange'], data_params['interval'])
                command.extend([
                    f"--exchange={data_params['exchange']}",
                    f"--interval={data_params['interval']}",
                    f"--portfolio-path={full_path}"
                ])
        if not data_params:
            return
        selected_metrics = _select_metrics_for_optimization()
        n_trials = ask(questionary.text, "Введите количество итераций на каждом шаге WFO (например, 100):",
                       default="100", validate=lambda text: text.isdigit() and int(text) > 0)
        total_periods = ask(questionary.text, "На сколько частей разделить всю историю (например, 10):", default="10",
                            validate=lambda text: text.isdigit() and int(text) > 1)
        train_periods = ask(questionary.text, f"Сколько частей использовать для обучения (1-{int(total_periods) - 1}):",
                            default="5", validate=lambda text: text.isdigit() and 0 < int(text) < int(total_periods))
        command.extend([
            "--strategy", strategy_name,
            "--rm", rm_type,
            "--metrics", *selected_metrics,
            "--n_trials", n_trials,
            "--total_periods", total_periods,
            "--train_periods", train_periods
        ])
        print("\nЗапускаю Walk-Forward Optimizer... Это может занять много времени.")
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
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION],
                       default="1min")
        rm_type = ask(questionary.select, "Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION],
                      default="FIXED")

        final_params = {}
        change_params = ask(
            questionary.confirm,
            "Хотите изменить параметры по умолчанию для live-запуска?",
            default=False
        )
        if change_params:
            strategy_class = strategies[strategy_name]
            rm_class = AVAILABLE_RISK_MANAGERS[rm_type]
            print("\n--- Настройка параметров стратегии ---")
            strat_params = _prompt_for_params(strategy_class.params_config)
            final_params.update(strat_params)
            print("\n--- Настройка параметров риск-менеджера ---")
            rm_params = _prompt_for_params(rm_class.params_config, prefix="rm_")
            final_params.update(rm_params)

        # Пути к скриптам остаются прежними
        command = [sys.executable, os.path.join("scripts", "run_live.py"), "--exchange", exchange, "--instrument",
                   instrument, "--interval", interval, "--strategy", strategy_name, "--rm", rm_type]

        if final_params:
            command.append(f"--params={json.dumps(final_params)}")

        if exchange == 'bybit':
            category = ask(questionary.select, "Выберите категорию рынка Bybit:",
                           choices=["linear", "spot", "inverse", GO_BACK_OPTION], default="linear")
            command.extend(["--category", category])

        print("\nЗапускаю live-бота... Нажмите Ctrl+C в этом окне, чтобы остановить.")
        subprocess.run(command)
    except UserCancelledError:
        print("\nОперация отменена.")


def run_dashboard():
    """Запуск Streamlit дашборда."""
    print("\n--- Запуск панели анализа (Dashboard) ---\n")
    command = ["streamlit", "run", os.path.join("app", "ui", "dashboard.py")]
    print("Чтобы остановить дашборд, нажмите Ctrl+C в этом окне.")
    subprocess.run(command)


def main():
    """Отображает главное меню и вызывает соответствующий обработчик."""
    menu_actions = {
        "1. Обновить списки ликвидных инструментов": run_update_lists,
        "2. Скачать исторические данные": run_download_data,
        "-----------------------------------------": None,
        "3. Запустить бэктест": run_backtest_flow,
        "4. Проанализировать результаты (Dashboard)": run_dashboard,
        "5. Запустить оптимизацию параметров (WFO)": run_optimizer,
        "------------------------------------------": None,
        "6. Запустить симуляцию в 'песочнице'": run_sandbox_trading,
        "Выход": "EXIT"
    }
    while True:
        choices_list = []
        i = 1
        pretty_to_original = {}
        for key, action in menu_actions.items():
            if action is None:
                choices_list.append(questionary.Separator(key))
            elif action == "EXIT":
                choices_list.append(key)
                pretty_to_original[key] = key
            else:
                clean_key = ". ".join(key.split(". ")[1:]) if ". " in key else key
                pretty_key = f"{i}. {clean_key}"
                choices_list.append(pretty_key)
                pretty_to_original[pretty_key] = key
                i += 1
        choice = questionary.select("Главное меню:", choices=choices_list, use_indicator=True).ask()
        if choice is None or choice == "Выход":
            print("Завершение работы.")
            break
        original_choice = pretty_to_original.get(choice)
        action = menu_actions.get(original_choice)
        if action and action != "EXIT":
            try:
                action()
                input("\nНажмите Enter, чтобы вернуться в главное меню...")
            except Exception as e:
                print(f"\nПроизошла критическая ошибка: {e}\n")
                input("\nНажмите Enter, чтобы вернуться в главное меню...")

if __name__ == "__main__":
    main()