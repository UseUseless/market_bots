"""
Запуск оптимизации параметров стратегии и риск-менеджера.

Использует метод Walk-Forward, который позволяет проверить
устойчивость параметров во времени и избежать переобучения.

Процесс включает:
1. Разделение истории на периоды (Train/Test).
2. Поиск лучших параметров на Train выборке с помощью Optuna.
3. Проверку этих параметров на Test выборке (Out-of-Sample).
4. Генерацию сводных отчетов и графиков.

Пример запуска:
    python scripts/run_optimization.py --strategy TripleFilter --exchange bybit --instrument BTCUSDT \
    --interval 1hour --total-periods 10 --train-periods 5
"""

import argparse
import logging
import sys
import os

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine.optimization.engine import WFOEngine
from app.shared.logging_setup import setup_global_logging
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk import RISK_MANAGEMENT_TYPES
from app.core.analysis.constants import METRIC_CONFIG
from app.shared.decorators import safe_entry

logger = logging.getLogger(__name__)


@safe_entry
def main() -> None:
    """
    Алгоритм работы:
    1. Настраивает логирование в режиме 'tqdm' для корректного отображения прогресс-баров.
    2. Парсит аргументы командной строки (параметры WFO, метрики, цели).
    3. Запускает `run_optimization_flow`, передавая словарь настроек.
    """
    # Включаем режим tqdm, чтобы логи не ломали прогресс-бар Optuna
    setup_global_logging(mode='tqdm', log_level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Скрипт для запуска Walk-Forward Optimization (WFO).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # --- Цель оптимизации ---
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instrument", type=str, help="Тикер инструмента (например, BTCUSDT).")
    group.add_argument("--portfolio-path", type=str, help="Путь к папке с .parquet файлами.")

    # --- Основные настройки ---
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'], help="Биржа.")
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм.")
    parser.add_argument(
        "--strategy", type=str, required=True,
        choices=list(AVAILABLE_STRATEGIES.keys()),
        help="Стратегия."
    )
    parser.add_argument(
        "--rm", dest="rm", type=str, default="FIXED",
        choices=list(RISK_MANAGEMENT_TYPES),
        help="Тип риск-менеджера."
    )

    # --- Настройки Optuna и WFO ---
    parser.add_argument(
        "--metrics", type=str, nargs='+', default=["calmar_ratio"],
        choices=list(METRIC_CONFIG.keys()),
        help="Целевые метрики."
    )

    parser.add_argument("--preload", action="store_true", help="Загрузить все данные в RAM (быстрее, но требует памяти).")
    
    # argparse автоматически сохранит их в переменные с подчеркиванием (args.n_trials)
    parser.add_argument("--n-trials", dest="n_trials", type=int, default=100, help="Итераций Optuna на шаг.")
    parser.add_argument("--total-periods", dest="total_periods", type=int, required=True, help="Всего частей истории.")
    parser.add_argument("--train-periods", dest="train_periods", type=int, required=True, help="Частей для обучения (Train).")
    parser.add_argument("--test-periods", dest="test_periods", type=int, default=1, help="Частей для теста (Test).")

    args = parser.parse_args()
    settings = vars(args)

    try:
        logger.info("--- Запуск потока Walk-Forward Optimization ---")

        # Создаем и запускаем оптимизатор напрямую
        optimizer = WFOEngine(settings)
        optimizer.run()

        logger.info("--- Поток Walk-Forward Optimization успешно завершен ---")

    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Ошибка подготовки WFO: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical("Произошла непредвиденная критическая ошибка!", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()