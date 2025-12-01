"""
CLI-скрипт для запуска одиночного бэктеста.

Этот скрипт служит точкой входа для тестирования одной конкретной стратегии
на одном инструменте и интервале. Он парсит аргументы командной строки
и передает управление в ядро симуляции (`app.core.engine.backtest`).

Результаты теста (сделки, метрики) будут сохранены в логи и могут быть
позже проанализированы через Dashboard.

Запуск:
    python scripts/run_backtest.py --strategy SimpleSMACross --exchange bybit --instrument BTCUSDT --interval 1hour
"""

import argparse
import logging
import sys
import os

# Добавляем корневую директорию проекта в sys.path для корректного импорта модулей app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine.backtest.runners import run_single_backtest_flow
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.shared.logging_setup import setup_global_logging


def main() -> None:
    """
    Основная функция запуска.

    Алгоритм работы:
    1. Настраивает глобальное логирование.
    2. Определяет доступные аргументы CLI на основе зарегистрированных стратегий
       и риск-менеджеров.
    3. Вызывает функцию-оркестратор `run_single_backtest_flow`, которая:
       - Инициализирует движок бэктеста.
       - Прогоняет симуляцию.
       - Генерирует отчеты (консоль, графики).
    """
    # Используем глобальный логгер для инициализации.
    # Специфичные настройки (фильтры времени симуляции) будут применены внутри flow.
    setup_global_logging()

    parser = argparse.ArgumentParser(
        description="Запуск одиночного бэктеста для торговой стратегии.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument(
        "--strategy",
        type=str,
        required=True,
        choices=list(AVAILABLE_STRATEGIES.keys()),
        help="Название стратегии из списка доступных."
    )
    parser.add_argument(
        "--exchange",
        type=str,
        required=True,
        choices=['tinkoff', 'bybit'],
        help="Биржа, данные которой будут использоваться."
    )
    parser.add_argument(
        "--instrument",
        type=str,
        required=True,
        help="Тикер инструмента (например, BTCUSDT или SBER)."
    )
    parser.add_argument(
        "--interval",
        type=str,
        required=True,
        help="Таймфрейм данных (например, 1min, 1hour)."
    )
    parser.add_argument(
        "--rm",
        dest="risk_manager_type",
        type=str,
        default="FIXED",
        choices=list(AVAILABLE_RISK_MANAGERS.keys()),
        help="Тип риск-менеджера."
    )

    args = parser.parse_args()

    # Преобразуем Namespace аргументов в словарь для передачи в движок
    settings = vars(args)

    try:
        run_single_backtest_flow(settings)
    except Exception as e:
        logging.getLogger(__name__).critical(
            f"Критическая ошибка при выполнении бэктеста: {e}", exc_info=True
        )
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nБэктест остановлен пользователем.")
        sys.exit(0)