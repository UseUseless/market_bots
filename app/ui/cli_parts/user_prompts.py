import questionary
import os
from typing import Dict, Optional, List, Type, Any
import logging

from . import dialogs as ui_helpers
from app.strategies.base_strategy import BaseStrategy
from app.analyzers.metrics.portfolio_metrics import METRIC_CONFIG
from config import PATH_CONFIG, EXCHANGE_INTERVAL_MAPS


class UserCancelledError(Exception):
    """Используется, когда пользователь отменяет ввод (нажимает Ctrl+C или 'Назад')."""
    pass


GO_BACK_OPTION = "Назад"

logger = logging.getLogger(__name__)

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
    """Инкапсулирует логику выбора одной или двух метрик для оптимизации."""
    mode = ask(
        questionary.select,
        "Выберите режим оптимизации:",
        choices=["Один критерий", "Два критерия (фронт Парето)", GO_BACK_OPTION],
        use_indicator=True
    )
    metric_choices = [
        questionary.Choice(
            title=f"{v['name']} ({v['direction']})", value=k, description=v['description']
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


def prompt_for_data_management() -> Optional[Dict[str, Any]]:
    """Проводит диалог для управления данными и возвращает словарь с настройками."""

    UPDATE_LISTS = "Обновить списки ликвидных инструментов"
    DOWNLOAD_DATA = "Скачать исторические данные"

    try:
        action = ask(
            questionary.select, "Выберите действие:",
            choices=[UPDATE_LISTS, DOWNLOAD_DATA, GO_BACK_OPTION]
        )

        if action == UPDATE_LISTS:
            exchange = ask(questionary.select, "Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION])
            return {"action": "update", "exchange": exchange}

        elif action == DOWNLOAD_DATA:
            download_mode = ask(
                questionary.select, "Что вы хотите скачать?",
                choices=["Отдельные тикеры (ручной ввод)", "Готовый список инструментов", GO_BACK_OPTION]
            )
            exchange = ask(questionary.select, "Выберите биржу:", choices=["tinkoff", "bybit", GO_BACK_OPTION])

            settings = {"action": "download", "exchange": exchange}

            if "Отдельные тикеры" in download_mode:
                instruments_str = ask(questionary.text, f"Введите тикеры для {exchange.upper()} через пробел:")
                settings["instrument"] = instruments_str.split()
            else:
                lists_dir = PATH_CONFIG["DATALISTS_DIR"]
                available_lists = [f for f in os.listdir(lists_dir) if
                                   f.startswith(exchange) and f.endswith('.txt')] if os.path.isdir(lists_dir) else []
                if not available_lists:
                    print(
                        f"Не найдено готовых списков для {exchange.upper()}. Создайте их через соответствующий пункт меню.")
                    return None
                selected_list = ask(questionary.select, "Выберите список для скачивания:",
                                    choices=[*available_lists, GO_BACK_OPTION])
                settings["list"] = selected_list

            available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
            interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION])
            days = ask(questionary.text, "Введите количество дней для загрузки:", default="365",
                       validate=lambda text: text.isdigit() and int(text) > 0)

            settings.update({"interval": interval, "days": int(days)})

            if exchange == 'bybit':
                category = ask(questionary.select, "Выберите категорию рынка Bybit:",
                               choices=["linear", "spot", "inverse", GO_BACK_OPTION], default="linear")
                settings["category"] = category

            return settings

    except UserCancelledError:
        logger.warning("Caught UserCancelledError, returning None.")
        return None
    return None

def prompt_for_backtest_settings() -> Optional[Dict[str, Any]]:
    """Проводит диалог для сбора настроек бэктеста и возвращает их."""
    try:
        strategies = get_available_strategies()
        if not strategies:
            print("Ошибка: не найдено ни одной доступной стратегии.")
            return None

        test_mode = ask(
            questionary.select, "Выберите режим запуска:",
            choices=["Один инструмент (выбор файла)", "Несколько инструментов (выбор папки)", GO_BACK_OPTION]
        )

        strategy_name = ask(questionary.select, "Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION])
        rm_type = ask(questionary.select, "Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION],
                      default="FIXED")

        settings = {"strategy": strategy_name, "risk_manager_type": rm_type}

        if "Один инструмент" in test_mode:
            data_params = ui_helpers.select_single_instrument()
            if not data_params: return None
            settings.update(data_params)
            settings["mode"] = "single"
        else:
            data_params = ui_helpers.select_instrument_folder()
            if not data_params: return None
            settings.update(data_params)
            settings["mode"] = "batch"

        return settings
    except UserCancelledError:
        return None


def prompt_for_optimization_settings() -> Optional[Dict[str, Any]]:
    """Проводит диалог для сбора настроек оптимизации и возвращает их."""
    try:
        strategies = get_available_strategies()
        if not strategies:
            print("Ошибка: не найдено ни одной доступной стратегии.")
            return None

        strategy_name = ask(questionary.select, "Выберите стратегию для оптимизации:",
                            choices=[*strategies.keys(), GO_BACK_OPTION])
        rm_type = ask(questionary.select, "Выберите риск-менеджер для оптимизации:",
                      choices=["FIXED", "ATR", GO_BACK_OPTION])
        opt_mode = ask(
            questionary.select, "Выберите объект оптимизации:",
            choices=["Один инструмент (выбор файла)", "Портфель инструментов (выбор папки)", GO_BACK_OPTION]
        )

        settings = {"strategy": strategy_name, "rm": rm_type}
        data_params = None

        if "Один инструмент" in opt_mode:
            data_params = ui_helpers.select_single_instrument()
            if data_params:
                settings.update(data_params)
        else:
            data_params = ui_helpers.select_instrument_folder()
            if data_params:
                full_path = os.path.join(PATH_CONFIG["DATA_DIR"], data_params['exchange'], data_params['interval'])
                settings.update(data_params)
                settings["portfolio_path"] = full_path

        if not data_params: return None

        selected_metrics = _select_metrics_for_optimization()
        n_trials = ask(questionary.text, "Количество итераций на каждом шаге WFO (например, 100):", default="100",
                       validate=lambda text: text.isdigit() and int(text) > 0)
        total_periods = ask(questionary.text, "На сколько частей разделить всю историю (например, 10):", default="10",
                            validate=lambda text: text.isdigit() and int(text) > 1)
        train_periods = ask(questionary.text, f"Сколько частей использовать для обучения (1-{int(total_periods) - 1}):",
                            default="5", validate=lambda text: text.isdigit() and 0 < int(text) < int(total_periods))

        settings.update({
            "metrics": selected_metrics,
            "n_trials": int(n_trials),
            "total_periods": int(total_periods),
            "train_periods": int(train_periods),
            "test_periods": 1
        })
        return settings
    except UserCancelledError:
        return None


def prompt_for_live_settings() -> Optional[Dict[str, Any]]:
    """Проводит диалог для сбора настроек live-режима."""
    try:
        strategies = get_available_strategies()
        if not strategies:
            print("Ошибка: не найдено ни одной доступной стратегии.")
            return None

        exchange = ask(questionary.select, "Выберите биржу:", choices=["bybit", "tinkoff", GO_BACK_OPTION])
        instrument = ask(questionary.text, f"Введите тикер для {exchange.upper()}:")
        strategy_name = ask(questionary.select, "Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION])
        live_intervals = ['1min', '3min', '5min', '15min']
        available_intervals = [i for i in live_intervals if i in EXCHANGE_INTERVAL_MAPS[exchange]]
        interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION],
                       default="1min")
        rm_type = ask(questionary.select, "Выберите риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION],
                      default="FIXED")

        settings = {
            "exchange": exchange, "instrument": instrument, "strategy": strategy_name,
            "interval": interval, "risk_manager_type": rm_type, "trade_mode": "SANDBOX"
        }

        if exchange == 'bybit':
            category = ask(questionary.select, "Выберите категорию рынка Bybit:",
                           choices=["linear", "spot", "inverse", GO_BACK_OPTION], default="linear")
            settings["category"] = category

        return settings
    except UserCancelledError:
        return None