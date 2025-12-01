"""
CLI-скрипт для запуска оптимизации параметров (Walk-Forward Optimization).

Этот скрипт управляет процессом подбора оптимальных параметров стратегии.
Использует метод Walk-Forward (шагающее окно), который позволяет проверить
устойчивость параметров во времени и избежать переобучения (overfitting).

Процесс включает:
1. Разделение истории на периоды (Train/Test).
2. Поиск лучших параметров на Train выборке с помощью Optuna.
3. Проверку этих параметров на Test выборке (Out-of-Sample).
4. Генерацию сводных отчетов и графиков.

Запуск:
    python scripts/run_optimization.py --strategy TripleFilter --exchange bybit --instrument BTCUSDT \
    --interval 1hour --total-periods 10 --train-periods 5
"""

import argparse
import logging
import sys
import os
from typing import List, Dict, Any

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine.optimization.runner import run_optimization_flow
from app.shared.logging_setup import setup_global_logging
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.core.analysis.constants import METRIC_CONFIG

logger = logging.getLogger(__name__)


def _get_available_choices(config_dict: Dict[str, Any]) -> List[str]:
    """Вспомогательная функция для получения списка ключей словаря."""
    return list(config_dict.keys())


def main() -> None:
    """
    Основная функция запуска оптимизации.

    Алгоритм работы:
    1. Настраивает логирование в режиме 'tqdm' для корректного отображения прогресс-баров.
    2. Парсит сложные аргументы командной строки (параметры WFO, метрики, цели).
    3. Запускает `run_optimization_flow`, передавая словарь настроек.
    4. Обрабатывает исключения и гарантирует восстановление настроек логирования при выходе.
    """
    # Включаем режим tqdm, чтобы логи не ломали прогресс-бар Optuna
    setup_global_logging(mode='tqdm', log_level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Скрипт для запуска Walk-Forward Optimization (WFO).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # --- Цель оптимизации (Инструмент или Портфель) ---
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--instrument",
        type=str,
        help="Тикер ОДНОГО инструмента для оптимизации (например, BTCUSDT)."
    )
    group.add_argument(
        "--portfolio-path",
        type=str,
        help="Путь к папке с .parquet файлами для портфельной оптимизации."
    )

    # --- Основные настройки ---
    parser.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа, данные которой используются."
    )
    parser.add_argument(
        "--interval", type=str, required=True,
        help="Таймфрейм данных (например, 1hour)."
    )
    parser.add_argument(
        "--strategy", type=str, required=True,
        choices=_get_available_choices(AVAILABLE_STRATEGIES),
        help="Стратегия для оптимизации."
    )
    parser.add_argument(
        "--rm", dest="risk_manager_type", type=str, default="FIXED",
        choices=_get_available_choices(AVAILABLE_RISK_MANAGERS),
        help="Тип риск-менеджера."
    )

    # --- Настройки Optuna и WFO ---
    parser.add_argument(
        "--metrics", type=str, nargs='+', default=["calmar_ratio"],
        choices=_get_available_choices(METRIC_CONFIG),
        help="Целевые метрики оптимизации (можно несколько для Multi-objective)."
    )
    parser.add_argument(
        "--n_trials", type=int, default=100,
        help="Количество итераций Optuna на каждом шаге WFO (In-Sample)."
    )
    parser.add_argument(
        "--total_periods", type=int, required=True,
        help="На сколько равных частей разделить весь доступный исторический датасет."
    )
    parser.add_argument(
        "--train_periods", type=int, required=True,
        help="Сколько частей использовать для обучения (In-Sample окно)."
    )
    parser.add_argument(
        "--test_periods", type=int, default=1,
        help="Сколько частей использовать для теста (Out-of-Sample шаг)."
    )

    args = parser.parse_args()
    settings = vars(args)

    try:
        run_optimization_flow(settings)

    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Ошибка подготовки WFO: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("\nОптимизация прервана пользователем. Завершение работы...")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Произошла непредвиденная ошибка в процессе WFO: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Восстанавливаем стандартный режим логирования перед выходом
        setup_global_logging(mode='default', log_level=logging.INFO)
        print("\nРабота скрипта завершена.")


if __name__ == "__main__":
    main()