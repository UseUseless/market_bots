import asyncio
import logging
from typing import Dict, Any

from app.engines.live_engine import LiveEngine
from app.utils.logging_setup import setup_global_logging

# Инициализируем логгер для этого модуля
logger = logging.getLogger(__name__)


def run_live_flow(settings: Dict[str, Any]):
    """
    Основная функция-оркестратор для запуска торгового бота в live-режиме.

    Эта функция является точкой входа для live-сессий, вызываемой как из
    интерактивного лаунчера, так и напрямую из скрипта. Она отвечает за:
    1. Настройку глобального логирования.
    2. Создание экземпляра LiveEngine с переданными настройками.
    3. Запуск асинхронного event loop'а.
    4. Корректную обработку остановки (включая Ctrl+C) и любых критических ошибок.

    :param settings: Словарь с полной конфигурацией для запуска, включая
                     'exchange', 'instrument', 'strategy', 'trade_mode' и т.д.
    """
    # Настраиваем глобальный логгер для вывода в консоль
    setup_global_logging()

    trade_mode = settings.get("trade_mode", "SANDBOX").upper()
    instrument = settings.get("instrument", "N/A")
    logger.info(f"--- Запуск потока Live Trading для {instrument} в режиме '{trade_mode}' ---")

    try:
        # Создаем экземпляр движка, передавая ему все настройки
        engine = LiveEngine(settings)

        # Запускаем главный асинхронный метод движка.
        # asyncio.run() автоматически создает и управляет event loop'ом.
        asyncio.run(engine.run())

    except KeyboardInterrupt:
        # Эта секция сработает, если пользователь нажмет Ctrl+C в консоли.
        # LiveEngine спроектирован так, чтобы корректно завершить свои задачи
        # при отмене, поэтому здесь достаточно просто информационного сообщения.
        logger.info("Поток Live Trading остановлен пользователем (KeyboardInterrupt).")

    except Exception as e:
        # Ловим любые другие непредвиденные ошибки, которые могли произойти
        # на этапе инициализации или выполнения.
        logger.critical(f"Критическая ошибка в потоке Live Trading: {e}", exc_info=True)

    finally:
        # Этот блок выполнится в любом случае: при нормальном завершении (маловероятно),
        # при остановке по Ctrl+C или при возникновении ошибки.

        # Даем небольшую паузу, чтобы все фоновые задачи (например, закрытие
        # сетевых соединений) успели корректно завершиться.
        try:
            # Используем asyncio.run для выполнения асинхронного sleep
            asyncio.run(asyncio.sleep(1))
        except RuntimeError:
            # Может возникнуть, если event loop уже закрыт; это нормально.
            pass

        logger.info("--- Поток Live Trading завершен ---")