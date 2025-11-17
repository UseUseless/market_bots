import asyncio
import logging
import argparse
from typing import Dict, Any

# 1. Импортируем нашу новую централизованную функцию-оркестратор
from app.flows.live_flow import run_live_flow

# 2. Импортируем необходимые компоненты для парсера аргументов
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS
from app.utils.logging_setup import setup_global_logging

# 3. Получаем экземпляр логгера для этого модуля
logger = logging.getLogger(__name__)


def main():
    """
    Точка входа для запуска live-бота из командной строки.
    Эта функция только парсит аргументы и передает их в основной "flow".
    """
    # Настраиваем глобальное логирование для вывода в консоль
    setup_global_logging()

    # --- Парсинг аргументов командной строки ---
    parser = argparse.ArgumentParser(
        description="Запуск торгового бота в live-режиме или песочнице."
    )
    parser.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа для запуска."
    )
    parser.add_argument(
        "--instrument", type=str, required=True,
        help="Тикер/символ инструмента для торговли."
    )
    parser.add_argument(
        "--interval", type=str, default="1min",
        help="Торговый интервал (например, 1min, 5min)."
    )
    parser.add_argument(
        "--category", type=str, default="linear",
        help="Категория рынка для Bybit (spot, linear, inverse)."
    )
    parser.add_argument(
        "--strategy", type=str, required=True,
        choices=list(AVAILABLE_STRATEGIES.keys()),
        help="Имя стратегии для использования."
    )
    parser.add_argument(
        "--rm", dest="risk_manager_type", type=str, default="FIXED",
        choices=list(AVAILABLE_RISK_MANAGERS.keys()),
        help="Модель управления риском."
    )
    parser.add_argument(
        "--mode", dest="trade_mode", type=str, default="SANDBOX",
        choices=["SANDBOX", "REAL"],
        help="Режим торговли: SANDBOX (песочница) или REAL (боевой)."
    )
    args = parser.parse_args()

    # Конвертируем Namespace от argparse в обычный словарь
    settings: Dict[str, Any] = vars(args)

    # --- Запуск основного потока приложения ---
    try:
        # Вызываем нашу централизованную функцию, передавая ей все настройки
        run_live_flow(settings)

    except KeyboardInterrupt:
        # Корректно обрабатываем остановку по Ctrl+C
        logger.info("Программа остановлена пользователем.")
    except Exception as e:
        # Ловим любые другие критические ошибки, которые могли возникнуть при запуске
        logger.critical(f"Критическая ошибка на верхнем уровне запуска: {e}", exc_info=True)
    finally:
        # Даем asyncio немного времени на корректное закрытие всех задач
        asyncio.run(asyncio.sleep(1))
        logger.info("--- Сессия Live Trading завершена ---")


if __name__ == "__main__":
    main()