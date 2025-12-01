"""
Модуль запуска оптимизации (Optimization Runner).

Служит точкой входа для процессов подбора параметров (WFO).
Этот модуль изолирует сложную логику настройки окружения (логирование,
обработка прерываний, внедрение зависимостей) от самой бизнес-логики оптимизации.

Основные задачи:
1.  Переключение логирования в режим совместимости с `tqdm` (прогресс-барами).
2.  Инициализация движка оптимизации (`OptimizationEngine`).
3.  Глобальная обработка ошибок и безопасное завершение (Graceful Shutdown).
"""

import logging
from typing import Dict, Any

from app.core.engine.optimization.engine import OptimizationEngine
from app.shared.logging_setup import setup_global_logging
from app.bootstrap.container import container

logger = logging.getLogger(__name__)


def run_optimization_flow(settings: Dict[str, Any]):
    """
    Оркестратор процесса Walk-Forward Optimization.

    Принимает "сырые" настройки из UI/CLI, настраивает глобальное окружение
    и запускает движок оптимизации. Гарантирует восстановление настроек
    логирования даже в случае критической ошибки.

    Args:
        settings (Dict[str, Any]): Словарь конфигурации. Ожидаемые ключи:
            - 'strategy': Имя стратегии.
            - 'instrument' / 'portfolio_path': Цель оптимизации.
            - 'exchange', 'interval': Параметры данных.
            - 'train_periods', 'test_periods', 'total_periods': Параметры WFO.
            - 'n_trials', 'metrics': Настройки Optuna.
    """
    # 1. Настройка окружения
    # Переключаем логирование в режим 'tqdm'. Это важно, так как оптимизация
    # выводит много прогресс-баров. Если оставить стандартный StreamHandler,
    # логи будут "разрывать" строки прогресса, делая вывод нечитаемым.
    setup_global_logging(mode='tqdm', log_level=logging.INFO)

    try:
        logger.info("--- Запуск потока Walk-Forward Optimization ---")

        # 2. Внедрение зависимостей (Dependency Injection)
        # Получаем готовый, инициализированный FeatureEngine из контейнера.
        feature_engine = container.feature_engine

        # Создаем движок. Он сам подготовит данные и разобьет их на периоды.
        engine = OptimizationEngine(settings, feature_engine)

        # 3. Запуск длительного процесса
        # Этот метод блокирует выполнение до завершения всех шагов WFO.
        engine.run()

        logger.info("--- Поток Walk-Forward Optimization успешно завершен ---")

    except (FileNotFoundError, ValueError) as e:
        # Ловим "ожидаемые" ошибки конфигурации (нет данных, неверные даты).
        # Логируем их как ERROR, но не даем приложению упасть с трейсбеком.
        logger.error(f"Ошибка подготовки или выполнения WFO: {e}")

    except KeyboardInterrupt:
        # Обработка Ctrl+C пользователем.
        # Важно перехватить это здесь, чтобы корректно выполнить блок finally.
        logger.warning("\nОптимизация прервана пользователем. Завершение работы...")

    except Exception:
        # Ловим все остальные, непредвиденные баги (NullPointer, SyntaxError в стратегии и т.д.).
        logger.critical("Произошла непредвиденная критическая ошибка в процессе WFO!", exc_info=True)

    finally:
        # 4. Очистка ресурсов (Teardown)
        # В любом случае (успех, ошибка или прерывание) мы ОБЯЗАНЫ вернуть
        # логирование в стандартный режим. Иначе меню Лаунчера перестанет работать
        # или будет выводиться криво.
        setup_global_logging(mode='default', log_level=logging.INFO)
        print("\nНастройки логирования восстановлены в стандартный режим.")