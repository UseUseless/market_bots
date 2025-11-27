import os
import importlib
import inspect
import logging
from typing import Dict, Type

from app.strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


def _discover_strategies() -> Dict[str, Type[BaseStrategy]]:
    """
    Динамически находит и загружает классы стратегий из подпапки 'logic'.
    """
    strategies_dict = {}

    # Определяем пути
    current_dir = os.path.dirname(__file__)
    logic_dir = os.path.join(current_dir, "logic")

    # Имя пакета для импорта (например, app.strategies)
    current_package = __package__

    if not os.path.exists(logic_dir):
        logger.warning(f"Папка со стратегиями не найдена: {logic_dir}")
        return {}

    for filename in os.listdir(logic_dir):
        # Пропускаем служебные файлы
        if filename.endswith(".py") and not filename.startswith("_"):

            # Формируем путь для импорта: app.strategies.logic.simple_sma_cross
            module_name = f"{current_package}.logic.{filename[:-3]}"

            try:
                strategy_module = importlib.import_module(module_name)

                # Ищем классы-наследники BaseStrategy
                for name, cls in inspect.getmembers(strategy_module, inspect.isclass):
                    if issubclass(cls, BaseStrategy) and cls is not BaseStrategy:
                        # Ключ - имя файла (как id стратегии)
                        strategy_key = filename[:-3]
                        strategies_dict[strategy_key] = cls
                        # logger.debug(f"Загружена стратегия: {strategy_key} -> {cls.__name__}")
                        break
            except ImportError as e:
                logger.error(f"Ошибка импорта стратегии {filename}: {e}")
            except Exception as e:
                logger.error(f"Ошибка при загрузке {filename}: {e}")

    return strategies_dict


# Экспортируем словарь доступных стратегий и базовый класс
AVAILABLE_STRATEGIES = _discover_strategies()
__all__ = ["AVAILABLE_STRATEGIES", "BaseStrategy"]