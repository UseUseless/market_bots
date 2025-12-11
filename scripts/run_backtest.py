"""
CLI-скрипт для запуска одиночного бэктеста.

Это скрипт для тестирования одной конкретной стратегии на одном инструменте и интервале.
Он парсит аргументы командной строки и передает управление в ядро симуляции (`app.core.engine.backtest`).

Результаты теста (сделки, метрики) будут сохранены в логи и могут быть позже проанализированы через Dashboard.

Пример запуска:
    python scripts/run_backtest.py --strategy SimpleSMACross --exchange bybit --instrument BTCUSDT --interval 1hour
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine.backtest.runners import run_single_backtest_flow
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk import RISK_MANAGEMENT_TYPES
from app.shared.decorators import safe_entry


@safe_entry
def main() -> None:
    """
    Алгоритм работы:
    1. Определяет доступные аргументы CLI на основе зарегистрированных стратегий
       и риск-менеджеров.
    2. Вызывает функцию-оркестратор `run_single_backtest_flow`, которая:
       - Инициализирует движок бэктеста.
       - Прогоняет симуляцию.
       - Генерирует отчеты (консоль, графики).
    """
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
        choices=list(RISK_MANAGEMENT_TYPES.keys()),
        help="Тип риск-менеджера."
    )

    args = parser.parse_args()
    args_settings = vars(args)

    run_single_backtest_flow(args_settings)


if __name__ == "__main__":
    main()