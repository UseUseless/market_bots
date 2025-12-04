"""
CLI-скрипт для запуска Сигналов (Live Signal Monitor).

Скрипт выполняет следующие задачи:
1. Запускает асинхронный оркестратор (`LiveOrchestrator`).
2. Поддерживает работу в режиме реального времени, обрабатывая сигналы
   согласно конфигурациям из базы данных.

Пример запуска:
    python scripts/run_signals.py
"""

import sys
import os

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.engine.live.orchestrator import run_live_monitor_flow
from app.shared.decorators import safe_entry


@safe_entry
def main() -> None:
    """
    Выполняет инициализацию окружения и передает управление ядру системы Live режима.
    """
    run_live_monitor_flow()


if __name__ == "__main__":
    main()