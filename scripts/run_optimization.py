import argparse
import logging
import sys
from typing import List

# 1. Импортируем "flow", который содержит всю реальную логику
from app.core.engine.optimization.runner import run_optimization_flow

# 2. Импортируем необходимые конфигурации и утилиты для парсера
from app.shared.logging_setup import setup_global_logging
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk_engine.risk_manager import AVAILABLE_RISK_MANAGERS
from app.core.analysis.constants import METRIC_CONFIG

logger = logging.getLogger(__name__)

def _get_available_choices(config_dict: dict) -> List[str]:
    """Вспомогательная функция для получения списка доступных ключей из словарей."""
    return list(config_dict.keys())

def main():
    """
    Точка входа для запуска WFO из командной строки.
    Эта функция только парсит аргументы и передает их в основной "flow".
    """
    # 3. Настраиваем логирование, совместимое с TQDM (прогресс-барами)
    setup_global_logging(mode='tqdm', log_level=logging.INFO)

    # 4. Определяем парсер аргументов. Его структура остается неизменной,
    # чтобы сохранить совместимость с предыдущими вызовами.
    parser = argparse.ArgumentParser(
        description="Скрипт для запуска Walk-Forward Optimization (WFO).",
        formatter_class=argparse.RawTextHelpFormatter
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instrument", type=str, help="Тикер ОДНОГО инструмента для оптимизации.")
    group.add_argument("--portfolio-path", type=str, help="Путь к папке с .parquet файлами для портфельной оптимизации.")

    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм для оптимизации.")
    parser.add_argument("--strategy", type=str, required=True, choices=_get_available_choices(AVAILABLE_STRATEGIES))
    parser.add_argument("--rm", dest="risk_manager_type", type=str, default="FIXED", choices=_get_available_choices(AVAILABLE_RISK_MANAGERS))

    parser.add_argument("--metrics", type=str, nargs='+', default=["calmar_ratio"], choices=_get_available_choices(METRIC_CONFIG))
    parser.add_argument("--n_trials", type=int, default=100, help="Количество итераций Optuna на каждом шаге WFO.")

    parser.add_argument("--total_periods", type=int, required=True, help="На сколько равных частей разделить весь датасет.")
    parser.add_argument("--train_periods", type=int, required=True, help="Сколько частей использовать для обучения (In-Sample).")
    parser.add_argument("--test_periods", type=int, default=1, help="Сколько частей использовать для теста (Out-of-Sample).")

    args = parser.parse_args()

    # 5. Преобразуем аргументы в словарь и вызываем flow с обработкой ошибок
    try:
        settings = vars(args)
        run_optimization_flow(settings)

    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Ошибка подготовки WFO: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("\nОптимизация прервана пользователем. Завершение работы...")
    except Exception:
        logger.critical("Произошла непредвиденная ошибка в процессе WFO!", exc_info=True)
        sys.exit(1)
    finally:
        # 6. В любом случае восстанавливаем стандартный режим логирования
        setup_global_logging(mode='default', log_level=logging.INFO)
        print("\nНастройки логирования восстановлены в стандартный режим.")


if __name__ == "__main__":
    main()