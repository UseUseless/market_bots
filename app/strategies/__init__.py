"""
Модуль-реестр торговых стратегий.

Отвечает за автоматическое обнаружение и загрузку всех доступных
стратегий из подпапки `app/strategies/_дирестория_со_стратегиями_ - сейчас catalog/`.
Чтобы добавить новую стратегию, достаточно создать файл в папке `catalog/`.
и унаследовать класс от `BaseStrategy` реализовав def _calculate_signals.

- **AVAILABLE_STRATEGIES**: Словарь `{имя_файла: КлассСтратегии}`, содержащий
      все успешно загруженные стратегии.
"""

import os
import importlib
import inspect
import logging
from typing import Dict, Type

from app.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

STRATEGIES_DIR_NAME = 'catalog'

def _discover_strategies() -> Dict[str, Type[BaseStrategy]]:
    """
    Поиск и динамический импорт классов стратегий.

    Алгоритм работы:
    1. Сканирует директорию `app/strategies/_дирестория_со_стратегиями_`.
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
    strategies_dir = os.path.join(current_dir, STRATEGIES_DIR_NAME)

    # Определяем имя текущего пакета для корректных относительных импортов
    current_package = __package__

    if not os.path.exists(strategies_dir):
        logger.warning(f"Папка со стратегиями не найдена: {strategies_dir}")
        return {}

    # Перебираем файлы в папке со стратегиями
    for filename in os.listdir(strategies_dir):
        # Фильтр: только .py файлы, исключая __init__.py и скрытые файлы
        if filename.endswith(".py") and not filename.startswith("_"):

            # Формируем путь импорта: app/strategies/_директория_со_стратегиями_.имя_файла
            module_name = f"{current_package}.{STRATEGIES_DIR_NAME}.{filename[:-3]}"

            try:
                # Импорт модуля
                strategy_module = importlib.import_module(module_name)

                # Интроспекция: ищем классы внутри модуля
                for name, cls in inspect.getmembers(strategy_module, inspect.isclass):
                    # Проверяем 3 условия:
                    # 1. Это стратегия.
                    # 2. Это не базовый класс.
                    # 3. Класс написан В ЭТОМ файле (а не импортирован из другого).
                    if (issubclass(cls, BaseStrategy)
                            and cls is not BaseStrategy
                            and cls.__module__ == module_name):
                        # Ключ = Имя файла (mean_reversion.py -> mean_reversion)
                        strategy_key = filename[:-3]
                        strategies_dict[strategy_key] = cls

                        # Нашли одну стратегию в файле — хватит.
                        # Переходим к следующему файлу.
                        break

            except ImportError as e:
                logger.error(f"Ошибка импорта модуля стратегии '{filename}': {e}")
            except Exception as e:
                logger.error(f"Ошибка при загрузке стратегии из '{filename}': {e}")

    return strategies_dict


# Код выполняется при импорте пакета app.strategies.
AVAILABLE_STRATEGIES = _discover_strategies()

__all__ = ["AVAILABLE_STRATEGIES", "BaseStrategy"]