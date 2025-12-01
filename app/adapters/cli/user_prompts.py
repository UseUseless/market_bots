"""
Интерактивные диалоги с пользователем (CLI Prompts).

Этот модуль отвечает за сбор и валидацию пользовательского ввода через
библиотеку `questionary`. Он формирует конфигурационные словари (settings)
для запуска различных режимов работы приложения (бэктест, оптимизация, загрузка данных).

Роль в архитектуре:
    Адаптер ввода (Input Adapter). Преобразует ответы пользователя в терминале
    в структуры данных, понятные ядру приложения.
"""

import os
import logging
from typing import Dict, Optional, List, Type, Any

import questionary

from . import dialogs as ui_helpers
from app.strategies.base_strategy import BaseStrategy
from app.core.analysis.constants import METRIC_CONFIG
from app.shared.primitives import ExchangeType
from app.shared.config import config

# Попытка импорта модуля документации (опционально)
try:
    from docs.help_texts import HELP_TOPICS
except ImportError:
    HELP_TOPICS = {}

PATH_CONFIG = config.PATH_CONFIG
EXCHANGE_INTERVAL_MAPS = config.EXCHANGE_INTERVAL_MAPS

# Константы для меню
GO_BACK_OPTION = "Назад"
OPT_SINGLE_CRITERION = "Один критерий"
OPT_MULTI_CRITERION = "Два критерия (фронт Парето)"

logger = logging.getLogger(__name__)


class UserCancelledError(Exception):
    """
    Исключение, выбрасываемое при отмене ввода пользователем.
    Позволяет чисто выйти из вложенных диалогов в главное меню.
    """
    pass


def ask(question_func, *args, **kwargs):
    """
    Обертка для вызова функций questionary с обработкой отмены.

    Если пользователь нажимает Ctrl+C или выбирает 'Назад',
    выбрасывается UserCancelledError.

    Args:
        question_func: Функция questionary (select, text, confirm и т.д.).
        *args, **kwargs: Аргументы для этой функции.

    Returns:
        Ответ пользователя.

    Raises:
        UserCancelledError: Если ввод отменен.
    """
    try:
        answer = question_func(*args, **kwargs).ask()
        if answer is None or answer == GO_BACK_OPTION:
            raise UserCancelledError()
        return answer
    except KeyboardInterrupt:
        raise UserCancelledError()


def get_available_strategies() -> Dict[str, Type[BaseStrategy]]:
    """
    Динамически находит и импортирует все доступные стратегии.
    Использует ленивый импорт, чтобы избежать циклических зависимостей.
    """
    from app.strategies import AVAILABLE_STRATEGIES
    return AVAILABLE_STRATEGIES


def _select_metrics_for_optimization() -> List[str]:
    """
    Диалог выбора метрик для оптимизации (WFO).

    Позволяет выбрать одну цель (например, maximize Sharpe) или две
    для построения фронта Парето (например, Sharpe vs Drawdown).

    Returns:
        List[str]: Список ключей метрик (например, ['sharpe_ratio', 'max_drawdown']).
    """
    mode = ask(
        questionary.select,
        "Выберите режим оптимизации:",
        choices=[OPT_SINGLE_CRITERION, OPT_MULTI_CRITERION, GO_BACK_OPTION],
        use_indicator=True
    )

    # Подготовка вариантов выбора с описанием
    metric_choices = [
        questionary.Choice(
            title=f"{v['name']} ({v['direction']})",
            value=k,
            description=v['description']
        ) for k, v in METRIC_CONFIG.items()
    ]

    # Поиск дефолтных значений для удобства
    default_metric_choice = next((c for c in metric_choices if c.value == "calmar_ratio"), None)

    if mode == OPT_SINGLE_CRITERION:
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
        # Исключаем первую метрику из списка для второй
        second_metric_choices = [c for c in metric_choices if c.value != first_metric]
        default_second = next((c for c in second_metric_choices if c.value == "max_drawdown"), None)

        second_metric = ask(
            questionary.select, "Выберите вторую метрику (Цель №2):",
            choices=second_metric_choices, use_indicator=True, default=default_second
        )
        return [first_metric, second_metric]


def prompt_for_data_management() -> Optional[Dict[str, Any]]:
    """
    Диалог управления данными (скачивание, обновление списков).

    Returns:
        Optional[Dict[str, Any]]: Словарь с настройками для `data_manager`.
    """
    UPDATE_LISTS = "Обновить списки ликвидных инструментов"
    DOWNLOAD_DATA = "Скачать исторические данные"

    try:
        action = ask(
            questionary.select, "Выберите действие:",
            choices=[UPDATE_LISTS, DOWNLOAD_DATA, GO_BACK_OPTION]
        )

        if action == UPDATE_LISTS:
            exchange = ask(
                questionary.select, "Выберите биржу:",
                choices=[ExchangeType.TINKOFF, ExchangeType.BYBIT, GO_BACK_OPTION]
            )
            return {"action": "update", "exchange": exchange}

        elif action == DOWNLOAD_DATA:
            download_mode = ask(
                questionary.select, "Что вы хотите скачать?",
                choices=["Отдельные тикеры (ручной ввод)", "Готовый список инструментов", GO_BACK_OPTION]
            )
            exchange = ask(
                questionary.select, "Выберите биржу:",
                choices=[ExchangeType.TINKOFF, ExchangeType.BYBIT, GO_BACK_OPTION]
            )

            settings = {"action": "download", "exchange": exchange}

            if "Отдельные тикеры" in download_mode:
                instruments_str = ask(questionary.text, f"Введите тикеры для {exchange.upper()} через пробел:")
                settings["instrument"] = instruments_str.split()
            else:
                lists_dir = PATH_CONFIG["DATALISTS_DIR"]
                available_lists = []
                if os.path.isdir(lists_dir):
                    available_lists = [f for f in os.listdir(lists_dir) if
                                       f.startswith(exchange) and f.endswith('.txt')]

                if not available_lists:
                    print(f"Не найдено списков для {exchange.upper()}. Создайте их через меню.")
                    return None

                selected_list = ask(questionary.select, "Выберите список:", choices=[*available_lists, GO_BACK_OPTION])
                settings["list"] = selected_list

            available_intervals = list(EXCHANGE_INTERVAL_MAPS[exchange].keys())
            interval = ask(questionary.select, "Выберите интервал:", choices=[*available_intervals, GO_BACK_OPTION])

            days = ask(
                questionary.text, "Количество дней загрузки:", default="365",
                validate=lambda text: text.isdigit() and int(text) > 0
            )

            settings.update({"interval": interval, "days": int(days)})

            if exchange == ExchangeType.BYBIT:
                category = ask(
                    questionary.select, "Категория рынка Bybit:",
                    choices=["linear", "spot", "inverse", GO_BACK_OPTION], default="linear"
                )
                settings["category"] = category

            return settings

    except UserCancelledError:
        return None


def prompt_for_backtest_settings(force_mode: str = None) -> Optional[Dict[str, Any]]:
    """
    Диалог настройки бэктеста.

    Args:
        force_mode (str): Принудительный режим ('single' или 'batch').
                          Используется, если скрипт запущен напрямую.

    Returns:
        Optional[Dict[str, Any]]: Настройки для запуска движка бэктеста.
    """
    try:
        strategies = get_available_strategies()
        if not strategies:
            print("Ошибка: не найдено ни одной доступной стратегии.")
            return None

        if force_mode:
            test_mode = "Один инструмент" if force_mode == "single" else "Несколько инструментов"
        else:
            test_mode = ask(
                questionary.select, "Выберите режим запуска:",
                choices=["Один инструмент (выбор файла)", "Несколько инструментов (выбор папки)", GO_BACK_OPTION]
            )

        strategy_name = ask(questionary.select, "Выберите стратегию:", choices=[*strategies.keys(), GO_BACK_OPTION])
        rm_type = ask(
            questionary.select, "Выберите риск-менеджер:",
            choices=["FIXED", "ATR", GO_BACK_OPTION], default="FIXED"
        )

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
    """
    Диалог настройки оптимизации (WFO).

    Returns:
        Optional[Dict[str, Any]]: Настройки для `OptimizationEngine`.
    """
    try:
        strategies = get_available_strategies()
        if not strategies:
            print("Ошибка: нет доступных стратегий.")
            return None

        strategy_name = ask(questionary.select, "Стратегия для оптимизации:",
                            choices=[*strategies.keys(), GO_BACK_OPTION])
        rm_type = ask(questionary.select, "Риск-менеджер:", choices=["FIXED", "ATR", GO_BACK_OPTION])

        opt_mode = ask(
            questionary.select, "Объект оптимизации:",
            choices=["Один инструмент (файл)", "Портфель (папка)", GO_BACK_OPTION]
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

        n_trials = ask(
            questionary.text, "Итераций на шаг (например, 100):", default="100",
            validate=lambda text: text.isdigit() and int(text) > 0
        )
        total_periods = ask(
            questionary.text, "Частей для разбиения истории (например, 10):", default="10",
            validate=lambda text: text.isdigit() and int(text) > 1
        )
        train_periods = ask(
            questionary.text, f"Частей для обучения (1-{int(total_periods) - 1}):", default="5",
            validate=lambda text: text.isdigit() and 0 < int(text) < int(total_periods)
        )

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
    """
    Подтверждение запуска Live-режима.
    """
    try:
        confirmation = ask(
            questionary.confirm,
            "Запустить монитор сигналов используя конфигурации из Базы Данных?",
            default=True
        )
        if not confirmation:
            return None
        return {}
    except UserCancelledError:
        return None


def prompt_for_help_topic() -> Optional[str]:
    """
    Показывает меню выбора темы помощи (если доступно).
    """
    if not HELP_TOPICS:
        print("Справка недоступна (файл docs/help_texts.py не найден).")
        return None

    try:
        choices = list(HELP_TOPICS.keys()) + [GO_BACK_OPTION]
        topic_key = ask(
            questionary.select,
            "Выберите раздел справки:",
            choices=choices,
            use_indicator=True
        )
        if topic_key == GO_BACK_OPTION:
            return None
        return topic_key
    except UserCancelledError:
        return None