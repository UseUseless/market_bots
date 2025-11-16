import asyncio
import logging
import argparse

from app.engines.live_engine import LiveEngine
from app.utils.logging_setup import setup_global_logging
from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS

def main():
    """
    Главная функция: парсит аргументы, собирает настройки
    и передает их в LiveEngine для запуска.
    """
    setup_global_logging()
    parser = argparse.ArgumentParser(description="Запуск торгового бота в live-режиме или песочнице.")
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True)
    parser.add_argument("--interval", type=str, default="1min")
    parser.add_argument("--category", type=str, default="linear", help="Категория рынка для Bybit.")
    parser.add_argument("--strategy", type=str, required=True, choices=list(AVAILABLE_STRATEGIES.keys()))
    parser.add_argument("--rm", dest="risk_manager_type", type=str, default="FIXED", choices=list(AVAILABLE_RISK_MANAGERS.keys()))
    # Добавляем аргумент для режима торговли
    parser.add_argument("--mode", dest="trade_mode", type=str, default="SANDBOX", choices=["SANDBOX", "REAL"])

    args = parser.parse_args()

    # Собираем все настройки в один словарь
    settings = {
        "exchange": args.exchange,
        "instrument": args.instrument,
        "interval": args.interval,
        "category": args.category,
        "strategy": args.strategy,
        "risk_manager_type": args.risk_manager_type,
        "trade_mode": args.trade_mode.upper()
    }

    try:
        # Создаем и запускаем движок
        engine = LiveEngine(settings)
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        logging.info("Программа остановлена пользователем.")
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске Live Engine: {e}", exc_info=True)
    finally:
        # Небольшая пауза для корректного завершения всех асинхронных задач
        asyncio.run(asyncio.sleep(1))


if __name__ == "__main__":
    main()