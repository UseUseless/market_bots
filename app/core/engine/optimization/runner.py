import logging
from typing import Dict, Any

from app.core.engine.optimization.engine import OptimizationEngine
from app.shared.logging_setup import setup_global_logging
from app.bootstrap.container import container

# Получаем логгер для этого модуля
logger = logging.getLogger(__name__)

def run_optimization_flow(settings: Dict[str, Any]):
    """
    Основная функция-оркестратор для запуска Walk-Forward Optimization.

    Эта функция является заменой логики из старого скрипта run_optimization.py.
    Она принимает готовый словарь с настройками и управляет всем процессом.

    :param settings: Словарь с полной конфигурацией для запуска WFO.
    """
    # 1. Настраиваем логирование специально для работы с progress bar'ами (tqdm).
    # Это предотвратит "замусоривание" вывода прогресс-баров логами.
    setup_global_logging(mode='tqdm', log_level=logging.INFO)

    try:
        logger.info("--- Запуск потока Walk-Forward Optimization ---")

        # 2. Создаем экземпляр движка, передавая ему все настройки.
        # Движок сам обработает и дополнит настройки (например, создаст instrument_list из portfolio_path).
        feature_engine = container.feature_engine
        engine = OptimizationEngine(settings, feature_engine)

        # 3. Запускаем основной, длительный процесс оптимизации.
        engine.run()

        logger.info("--- Поток Walk-Forward Optimization успешно завершен ---")

    except (FileNotFoundError, ValueError) as e:
        # Ловим ожидаемые ошибки (например, не найдены файлы данных или некорректные параметры WFO).
        logger.error(f"Ошибка подготовки или выполнения WFO: {e}")
        # Мы не прерываем программу, а просто логируем ошибку,
        # чтобы управление вернулось в лаунчер.
    except KeyboardInterrupt:
        # Позволяем пользователю корректно прервать процесс через Ctrl+C.
        logger.warning("\nОптимизация прервана пользователем. Завершение работы...")
    except Exception:
        # Ловим все остальные, непредвиденные ошибки для диагностики.
        logger.critical("Произошла непредвиденная критическая ошибка в процессе WFO!", exc_info=True)
    finally:
        # 4. В любом случае (успех, ошибка, прерывание) восстанавливаем
        # стандартный режим логирования для последующих команд в лаунчере.
        setup_global_logging(mode='default', log_level=logging.INFO)
        # Выводим сообщение в stdout, чтобы пользователь видел, что процесс завершился.
        print("\nНастройки логирования восстановлены в стандартный режим.")