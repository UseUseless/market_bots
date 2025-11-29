"""
Модуль-реестр торговых стратегий.

Этот модуль отвечает за автоматическое обнаружение и загрузку всех доступных
торговых алгоритмов из подпапки `logic/`. Реализует паттерн "Плагин":
чтобы добавить новую стратегию, достаточно создать файл в папке `logic`
и унаследовать класс от `BaseStrategy`.

Состав:
    - **AVAILABLE_STRATEGIES**: Словарь `{имя_файла: КлассСтратегии}`, содержащий
      все успешно загруженные стратегии. Является точкой доступа для лаунчера и бэктестера.
    - **BaseStrategy**: Экспортируется для удобства импорта в других модулях.
"""

import os
import importlib
import inspect
import logging
from typing import Dict, Type

from app.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


def _discover_strategies() -> Dict[str, Type[BaseStrategy]]:
    """
    Выполняет поиск и динамический импорт классов стратегий.

    Алгоритм работы:
    1. Сканирует директорию `app/strategies/logic`.
    2. Импортирует каждый найденный `.py` файл как модуль.
    3. Ищет внутри модуля класс, который наследуется от `BaseStrategy`
       (но не является самим `BaseStrategy`).
    4. Собирает найденные классы в словарь.

    Returns:
        Dict[str, Type[BaseStrategy]]: Словарь, где ключ — имя файла (без .py),
        а значение — ссылка на класс стратегии.
    """
    strategies_dict = {}

    # Определяем абсолютные пути для сканирования
    current_dir = os.path.dirname(__file__)
    logic_dir = os.path.join(current_dir, "logic")

    # Определяем имя текущего пакета для корректных относительных импортов
    current_package = __package__

    if not os.path.exists(logic_dir):
        logger.warning(f"Папка со стратегиями не найдена: {logic_dir}")
        return {}

    # Перебираем файлы в папке logic
    for filename in os.listdir(logic_dir):
        # Фильтр: только .py файлы, исключая __init__.py и скрытые файлы
        if filename.endswith(".py") and not filename.startswith("_"):

            # Формируем путь импорта: app.strategies.logic.имя_файла
            module_name = f"{current_package}.logic.{filename[:-3]}"

            try:
                # Динамический импорт модуля
                strategy_module = importlib.import_module(module_name)

                # Интроспекция: ищем классы внутри модуля
                for name, cls in inspect.getmembers(strategy_module, inspect.isclass):
                    # Проверяем, что это наша стратегия (наследник BaseStrategy)
                    if issubclass(cls, BaseStrategy) and cls is not BaseStrategy:
                        # Используем имя файла как уникальный идентификатор стратегии
                        strategy_key = filename[:-3]
                        strategies_dict[strategy_key] = cls
                        break
            except ImportError as e:
                logger.error(f"Ошибка импорта модуля стратегии '{filename}': {e}")
            except Exception as e:
                logger.error(f"Ошибка при загрузке стратегии из '{filename}': {e}")

    return strategies_dict


# Единая точка доступа к списку стратегий.
# Код выполняется один раз при импорте пакета app.strategies.
AVAILABLE_STRATEGIES = _discover_strategies()

__all__ = ["AVAILABLE_STRATEGIES", "BaseStrategy"]