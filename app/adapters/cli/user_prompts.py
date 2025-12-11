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
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Dict, Optional, List, Type, Any

import questionary

from app.strategies.base_strategy import BaseStrategy
from app.core.analysis.constants import METRIC_CONFIG
from app.core.risk import RISK_MANAGEMENT_TYPES
from app.shared.primitives import ExchangeType
from app.shared.config import config
from docs.help_texts import HELP_TOPICS


PATH_CONFIG = config.PATH_CONFIG
EXCHANGE_INTERVAL_MAPS = config.EXCHANGE_INTERVAL_MAPS
DATA_LOADER_CONFIG = config.DATA_LOADER_CONFIG

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


def _initialize_tk() -> tk.Tk:
    """
    Инициализирует скрытое корневое окно Tkinter.

    Необходимо для корректного отображения диалоговых окон без запуска
    полноценного графического интерфейса приложения.

    Returns:
        tk.Tk: Объект корневого окна.
    """
    root = tk.Tk()
    root.withdraw()  # Скрываем главное окно, оставляем только диалог

    # Хак, чтобы окно диалога появилось поверх всех окон (особенно актуально для Windows)
    root.wm_attributes("-topmost", 1)
    root.update()
    return root


def select_single_instrument() -> Optional[Dict[str, str]]:
    """
    Открывает диалог выбора одного файла данных (.parquet).

    Выполняет валидацию выбранного файла:
    1. Проверяет наличие парного файла метаданных (.json).
    2. Пытается определить биржу, интервал и тикер из структуры папок.

    Returns:
        Optional[Dict[str, str]]: Словарь с параметрами инструмента:
            {
                "exchange": "tinkoff",
                "interval": "5min",
                "instrument": "SBER"
            }
            Возвращает None, если пользователь отменил выбор или файл некорректен.
    """
    root = _initialize_tk()

    filepath_str = filedialog.askopenfilename(
        title="Выберите .parquet файл с историческими данными",
        filetypes=[("Parquet files", "*.parquet")]
    )

    root.destroy()  # Освобождаем ресурсы GUI

    if not filepath_str:
        print("\nВыбор файла отменен.")
        return None

    filepath = Path(filepath_str)

    # 1. Проверка целостности данных (должен быть JSON с лотностью)
    json_path = filepath.with_suffix('.json')
    if not json_path.exists():
        print(f"\n[Ошибка] Файл метаданных не найден: {json_path.name}")
        print("Для каждого .parquet файла должен существовать .json файл с тем же именем.")
        return None

    # 2. Автоматическое определение параметров из пути
    # Ожидаемая структура: .../data/{exchange}/{interval}/{instrument}.parquet
    try:
        instrument = filepath.stem  # Имя файла без расширения
        interval = filepath.parent.name  # Папка интервала
        exchange = filepath.parent.parent.name  # Папка биржи

        if not instrument or not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить структуру папок.")
        print(f"Файл должен находиться в пути вида: '.../data/{{exchange}}/{{interval}}/'")
        return None

    print(f"\nВыбран инструмент: {exchange.upper()} / {interval} / {instrument.upper()}")

    return {
        "exchange": exchange,
        "interval": interval,
        "instrument": instrument
    }


def select_instrument_folder() -> Optional[Dict[str, str]]:
    """
    Открывает диалог выбора папки с данными для пакетного тестирования.

    Используется, когда нужно запустить стратегию сразу на всех инструментах
    внутри выбранной директории (например, на всех акциях с 5-минутным таймфреймом).

    Returns:
        Optional[Dict[str, str]]: Словарь с параметрами группы:
            {
                "exchange": "tinkoff",
                "interval": "5min"
            }
            Возвращает None при отмене или ошибке.
    """
    root = _initialize_tk()

    dirpath_str = filedialog.askdirectory(
        title="Выберите папку с данными (например, '.../data/tinkoff/5min')"
    )

    root.destroy()

    if not dirpath_str:
        print("\nВыбор папки отменен.")
        return None

    dirpath = Path(dirpath_str)

    # Предупреждение, если папка пуста (но не блокировка, так как может быть дозагрузка)
    if not any(dirpath.glob('*.parquet')):
        print(f"\n[Предупреждение] В папке '{dirpath.name}' не найдено .parquet файлов.")

    try:
        interval = dirpath.name
        exchange = dirpath.parent.name

        if not interval or not exchange:
            raise IndexError

    except IndexError:
        print("\n[Ошибка] Не удалось определить параметры из пути к папке.")
        print(f"Папка должна соответствовать структуре: '.../data/{{exchange}}/{{interval}}/'")
        return None

    print(f"\nВыбрана папка: {exchange.upper()} / {interval}")

    return {
        "exchange": exchange,
        "interval": interval
    }


def ask(question_func, *args, **kwargs):
    """
    Обертка для вызова функций questionary с обработкой отмены.
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
    """
    from app.strategies import AVAILABLE_STRATEGIES
    return AVAILABLE_STRATEGIES


def _select_metrics_for_optimization() -> List[str]:
    """
    Диалог выбора метрик для оптимизации (WFO).
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
    """
    UPDATE_LISTS = "Обновить списки ликвидных инструментов"
    DOWNLOAD_DATA = "Скачать исторические данные"

    try:
        selected_action_text = ask(
            questionary.select, "Выберите действие:",
            choices=[UPDATE_LISTS, DOWNLOAD_DATA, GO_BACK_OPTION]
        )

        # Маппинг текста меню на команды скрипта manage_data.py
        if selected_action_text == UPDATE_LISTS:
            exchange = ask(
                questionary.select, "Выберите биржу:",
                choices=[ExchangeType.TINKOFF, ExchangeType.BYBIT, GO_BACK_OPTION]
            )
            return {"action": "update", "exchange": exchange}

        elif selected_action_text == DOWNLOAD_DATA:
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
                # manage_data.py ожидает список для --instrument
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

            # DRY: Берем дефолтное кол-во дней из конфига
            default_days = str(DATA_LOADER_CONFIG["DAYS_TO_LOAD"])
            days = ask(
                questionary.text, "Количество дней загрузки:", default=default_days,
                validate=lambda text: text.isdigit() and int(text) > 0
            )

            settings.update({"interval": interval, "days": int(days)})

            if exchange == ExchangeType.BYBIT:
                # DRY: Берем дефолтную категорию из конфига
                default_cat = config.EXCHANGE_SPECIFIC_CONFIG[ExchangeType.BYBIT]["DEFAULT_CATEGORY"]
                category = ask(
                    questionary.select, "Категория рынка Bybit:",
                    choices=["linear", "spot", "inverse", GO_BACK_OPTION], default=default_cat
                )
                settings["category"] = category

            return settings

    except UserCancelledError:
        return None


def prompt_for_backtest_settings(force_mode: str = None) -> Optional[Dict[str, Any]]:
    """
    Диалог настройки бэктеста.
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

        # Динамический список риск-менеджеров
        rm_options = RISK_MANAGEMENT_TYPES + [GO_BACK_OPTION]
        rm_type = ask(
            questionary.select, "Выберите риск-менеджер:",
            choices=rm_options, default="FIXED"
        )

        settings = {"strategy": strategy_name, "risk_manager_type": rm_type}

        if "Один инструмент" in test_mode:
            data_params = select_single_instrument()
            if not data_params: return None
            settings.update(data_params)
            settings["mode"] = "single"
        else:
            data_params = select_instrument_folder()
            if not data_params: return None
            settings.update(data_params)
            settings["mode"] = "batch"

        return settings
    except UserCancelledError:
        return None


def prompt_for_optimization_settings() -> Optional[Dict[str, Any]]:
    """
    Диалог настройки оптимизации (WFO).
    """
    try:
        strategies = get_available_strategies()
        if not strategies:
            print("Ошибка: нет доступных стратегий.")
            return None

        strategy_name = ask(questionary.select, "Стратегия для оптимизации:",
                            choices=[*strategies.keys(), GO_BACK_OPTION])

        # Динамический список риск-менеджеров
        rm_options = RISK_MANAGEMENT_TYPES + [GO_BACK_OPTION]
        rm_type = ask(questionary.select, "Риск-менеджер:", choices=rm_options)

        opt_mode = ask(
            questionary.select, "Объект оптимизации:",
            choices=["Один инструмент (файл)", "Портфель (папка)", GO_BACK_OPTION]
        )

        settings = {"strategy": strategy_name, "rm": rm_type}
        data_params = None

        if "Один инструмент" in opt_mode:
            data_params = select_single_instrument()
            if data_params:
                settings.update(data_params)
        else:
            data_params = select_instrument_folder()
            if data_params:
                full_path = os.path.join(PATH_CONFIG["DATA_DIR"], data_params['exchange'], data_params['interval'])
                settings.update(data_params)
                # Для скрипта run_optimization.py ключ должен быть portfolio_path
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
