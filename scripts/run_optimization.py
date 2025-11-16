import argparse
import logging
import sys
from typing import List

from app.engines.optimization_engine import OptimizationEngine
from app.utils.logging_setup import setup_global_logging
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS
from app.analyzers.metrics.portfolio_metrics import METRIC_CONFIG

logger = logging.getLogger()

def _get_available_choices(config_dict: dict) -> List[str]:
    """Вспомогательная функция для получения списка доступных ключей из словарей."""
    return list(config_dict.keys())


def main():
    """
    Главная функция-обертка.
    Парсит аргументы командной строки, собирает их в словарь настроек
    и передает в движок оптимизации для выполнения.
    """
    # 1. Настраиваем логирование для работы с progress bar'ами (tqdm)
    setup_global_logging(mode='tqdm', log_level=logging.INFO)

    # 2. Определяем и парсим аргументы командной строки
    parser = argparse.ArgumentParser(
        description="Скрипт для запуска Walk-Forward Optimization (WFO).",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # --- Группа для выбора инструмента/портфеля ---
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--instrument",
        type=str,
        help="Тикер/символ ОДНОГО инструмента для оптимизации."
    )
    group.add_argument(
        "--portfolio-path",
        type=str,
        help="Путь к папке с .parquet файлами для портфельной оптимизации."
    )

    # --- Основные параметры бэктеста ---
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм для оптимизации.")
    parser.add_argument(
        "--strategy",
        type=str,
        required=True,
        choices=_get_available_choices(AVAILABLE_STRATEGIES),
        help="Имя стратегии для оптимизации."
    )
    parser.add_argument(
        "--rm",
        type=str,
        default="FIXED",
        choices=_get_available_choices(AVAILABLE_RISK_MANAGERS),
        help="Тип риск-менеджера."
    )

    # --- Параметры оптимизации ---
    parser.add_argument(
        "--metrics",
        type=str,
        nargs='+',
        default=["calmar_ratio"],
        choices=_get_available_choices(METRIC_CONFIG),
        help="Одна или несколько целевых метрик для оптимизации (для мульти-объективной)."
    )
    parser.add_argument(
        "--n_trials",
        type=int,
        default=100,
        help="Количество итераций Optuna на каждом шаге WFO."
    )

    # --- Параметры Walk-Forward сплиттера ---
    parser.add_argument(
        "--total_periods",
        type=int,
        required=True,
        help="На сколько равных частей разделить весь датасет."
    )
    parser.add_argument(
        "--train_periods",
        type=int,
        required=True,
        help="Сколько частей из total_periods использовать для обучения (In-Sample)."
    )
    parser.add_argument(
        "--test_periods",
        type=int,
        default=1,
        help="Сколько частей использовать для теста (Out-of-Sample). По умолчанию: 1."
    )

    args = parser.parse_args()

    # 3. Запускаем движок с обработкой исключений
    try:
        # Конвертируем Namespace от argparse в обычный словарь
        settings = vars(args)

        # Создаем экземпляр движка и передаем ему все настройки
        engine = OptimizationEngine(settings)

        # Запускаем процесс
        engine.run()

    except (FileNotFoundError, ValueError) as e:
        # Ловим ожидаемые ошибки (например, не найдены файлы данных)
        logger.error(f"Ошибка подготовки WFO: {e}")
        sys.exit(1)  # Выходим с кодом ошибки
    except KeyboardInterrupt:
        # Позволяем пользователю корректно прервать процесс
        logger.warning("\nОптимизация прервана пользователем. Завершение работы...")
    except Exception:
        # Ловим все остальные, непредвиденные ошибки
        logger.critical("Произошла непредвиденная ошибка в процессе WFO!", exc_info=True)
        sys.exit(1)
    finally:
        # 4. В любом случае (успех, ошибка, прерывание) восстанавливаем
        # стандартный режим логирования для последующих команд.
        setup_global_logging(mode='default', log_level=logging.INFO)
        print("\nНастройки логирования восстановлены в стандартный режим.")


if __name__ == "__main__":
    main()