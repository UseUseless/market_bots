"""
Скрипт для запуска сигналов.

1. Запускает асинхронный оркестратор.
2. Обрабатывает сигналы по стратегиям, определенным в БД и привязанные к ботам.
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
    Передает управление ядру системы Live режима.
    """
    run_live_monitor_flow()


if __name__ == "__main__":
    main()