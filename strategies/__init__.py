import os
import importlib
import inspect
from typing import Dict, Type

from .base_strategy import BaseStrategy


def _discover_strategies() -> Dict[str, Type[BaseStrategy]]:
    """
    Динамически находит и загружает все классы стратегий из текущей директории.
    Ключом словаря является имя файла (без .py), значением - сам класс стратегии.
    """
    strategies_dict = {}
    current_dir = os.path.dirname(__file__)

    for filename in os.listdir(current_dir):
        # Пропускаем служебные файлы и базовый класс
        if filename.endswith(".py") and not filename.startswith("_") and "base_strategy" not in filename:

            # 1. Формируем имя модуля для импорта (например, 'strategies.triple_filter')
            module_name = f"strategies.{filename[:-3]}"

            try:
                # 2. Динамически импортируем модуль
                strategy_module = importlib.import_module(module_name)

                # 3. Ищем внутри модуля классы, которые являются наследниками BaseStrategy
                for name, cls in inspect.getmembers(strategy_module, inspect.isclass):
                    if issubclass(cls, BaseStrategy) and cls is not BaseStrategy:
                        # 4. Нашли! Добавляем в словарь.
                        strategy_key = filename[:-3]  # 'triple_filter.py' -> 'triple_filter'
                        strategies_dict[strategy_key] = cls
                        break  # Предполагаем, что в одном файле одна стратегия
            except ImportError as e:
                print(f"Предупреждение: Не удалось импортировать стратегию из файла {filename}: {e}")

    return strategies_dict


# --- ГЛАВНАЯ ЧАСТЬ ---
# Вызываем функцию при импорте пакета и сохраняем результат в переменную,
# доступную на уровне всего пакета.
AVAILABLE_STRATEGIES = _discover_strategies()

# Добавляем __all__, чтобы было понятно, что именно экспортирует этот пакет
__all__ = ["AVAILABLE_STRATEGIES", "BaseStrategy"]