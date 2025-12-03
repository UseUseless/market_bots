"""
CLI-скрипт для запуска Монитора Сигналов (Live Signal Monitor).

Это точка входа для режима реального времени. Скрипт запускает асинхронный
Оркестратор (`LiveOrchestrator`), который управляет всем процессом.

ОБНОВЛЕНО: Добавлен механизм Single Instance Lock для предотвращения TelegramConflictError.
"""

import argparse
import logging
import sys
import os

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine.live.orchestrator import run_live_monitor_flow
from app.shared.logging_setup import setup_global_logging


def main() -> None:

    setup_global_logging()

    parser = argparse.ArgumentParser(
        description="Запуск Монитора Сигналов (Signal Monitor).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    args = parser.parse_args()

    try:
        run_live_monitor_flow(vars(args))

    except KeyboardInterrupt:
        print("\n🛑 Монитор сигналов остановлен пользователем.")
    except Exception as e:
        logging.getLogger(__name__).critical(
            f"Критическая ошибка в работе монитора: {e}", exc_info=True
        )
    finally:
        sys.exit(0)


if __name__ == "__main__":
    main()