import argparse

from app.core.engine.live.orchestrator import run_live_monitor_flow
from app.shared.logging_setup import setup_global_logging


def main():
    """
    Запускает режим 'Сигналы'.
    Подключается к бирже, следит за рынком, шлет уведомления в Telegram.
    Сделки НЕ совершаются.
    """
    setup_global_logging()

    parser = argparse.ArgumentParser(
        description="Запуск Монитора Сигналов (Signal Monitor). Режим: READ-ONLY (без торговли)."
    )
    # В будущем сюда можно добавить аргументы, например --verbose
    args = parser.parse_args()

    # Передаем настройки в движок
    # В будущем мы можем явно передать флаг: trade_execution=False
    run_live_monitor_flow(vars(args))


if __name__ == "__main__":
    main()