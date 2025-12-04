"""
Модуль-декоратор для безопасного запуска скриптов.
"""

import sys
import asyncio
import logging
import inspect
import functools
from typing import Callable, Any

from app.shared.logging_setup import setup_global_logging

def safe_entry(func: Callable) -> Callable:
    """
    Декоратор для main-функций CLI скриптов и лаунчера.

    Автоматически выполняет:
    1. Устанавливает `WindowsSelectorEventLoopPolicy` для Windows,
        чтобы избежать ошибок `RuntimeError: Event loop is closed` при работе с БД.
    2. Инициализирует глобальное логирование.
    3. Определение типа функции (async/sync) и корректный запуск.
    4. Глобальный перехват ошибок (Try/Except).

    Args:
        func (Callable): Целевая функция `main` (может быть как `def`, так и `async def`).

    Returns:
        Callable: Обернутая функция, готовая к запуску в блоке `if __name__ == "__main__":`.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        # Windows Fix
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        # Логирование
        setup_global_logging()
        logger = logging.getLogger("script_runner")

        # Запуск
        try:
            # Проверяем, является ли функция асинхронной (async def)
            if inspect.iscoroutinefunction(func):
                return asyncio.run(func(*args, **kwargs))
            else:
                return func(*args, **kwargs)

        except KeyboardInterrupt:
            print("\n🛑 Скрипт остановлен пользователем.")
            sys.exit(0)

        except Exception as e:
            logger.critical(f"🔥 Критическая ошибка: {e}", exc_info=True)
            sys.exit(1)

    return wrapper