"""
Оркестратор запуска Live-режима (Live Monitor Runner).

Этот модуль является точкой входа для запуска системы в режиме реального времени.
Он выполняет роль "сборщика" (Assembler) и "дирижера": связывает инфраструктуру (БД, Биржи)
с ядром (SignalEngine), управляет жизненным циклом приложения и обработкой сигналов ОС.

Архитектура запуска:
    DB (Settings) -> Orchestrator (Merge Logic) -> TradingConfig -> Strategy/Feed -> Engine.
"""

import asyncio
import logging
import queue
import signal
import sys
from typing import Tuple, Any, List, Dict

# Импорты схем и БД
from app.shared.schemas import TradingConfig
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import ConfigRepository
from app.infrastructure.database.models import StrategyConfig

# Глобальные зависимости
from app.bootstrap.container import container
from app.core.engine.live.engine import SignalEngine
from app.infrastructure.feeds.live.provider import LiveDataProvider

# Обработчики сигналов (Signal Handlers)
from app.adapters.cli.signal_viewer import ConsoleSignalViewer
from app.infrastructure.database.signal_logger import DBSignalLogger
from app.adapters.telegram.publisher import TelegramSignalSender

# Стратегии и утилиты
from app.strategies import AVAILABLE_STRATEGIES
from app.shared.logging_setup import setup_global_logging

logger = logging.getLogger(__name__)


def _assemble_config(db_config: StrategyConfig) -> TradingConfig:
    """
    Фабричный метод сборки конфигурации из данных БД.

    Реализует логику слияния параметров:
    1. Получает класс стратегии по имени.
    2. Извлекает дефолтные параметры из кода (Hardcoded defaults).
    3. Накладывает параметры из БД (User overrides).
    4. Упаковывает всё в чистый DTO `TradingConfig`.

    Args:
        db_config: ORM-объект конфигурации из базы данных.

    Returns:
        TradingConfig: Готовый к использованию объект конфигурации.
    """
    # 1. Поиск класса стратегии
    StrategyClass = AVAILABLE_STRATEGIES.get(db_config.strategy_name)
    if not StrategyClass:
        # Критическая ошибка конфигурации, стратегия не может быть запущена
        raise ValueError(f"Strategy class '{db_config.strategy_name}' not found in registry")

    # 2. Слияние параметров (Merge Strategy)
    # Дефолты из кода + JSON из базы данных
    final_params = StrategyClass.get_default_params()
    if db_config.parameters:
        final_params.update(db_config.parameters)

    # 3. Подготовка конфига риска
    # В БД хранится строка типа 'FIXED', преобразуем в словарь для схемы
    risk_config = {"type": db_config.risk_manager_type or "FIXED"}

    # 4. Создание DTO
    # initial_capital ставим заглушкой, так как в режиме сигналов мы не торгуем реальным депозитом,
    # но схема требует это поле.
    return TradingConfig(
        mode="LIVE",
        exchange=db_config.exchange,
        instrument=db_config.instrument,
        interval=db_config.interval,
        strategy_name=db_config.strategy_name,
        strategy_params=final_params,
        risk_config=risk_config,
        initial_capital=10000.0
    )


async def _config_loader() -> List[StrategyConfig]:
    """
    Callback-функция для загрузки активных стратегий.

    Передается в движок, чтобы он мог периодически опрашивать БД
    на предмет появления новых или остановки старых стратегий.

    Returns:
        List[StrategyConfig]: Список активных конфигураций.
    """
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        configs = await repo.get_active_strategies()
        return configs


async def _pair_builder(db_config: StrategyConfig) -> Tuple[LiveDataProvider, Any]:
    """
    Callback-фабрика для создания рабочих объектов.

    Вызывается движком, когда он обнаруживает новую активную стратегию в БД.
    Здесь происходит Dependency Injection.

    Args:
        db_config: ORM-объект конфигурации.

    Returns:
        Tuple[LiveDataProvider, BaseStrategy]: Пара (Поток данных, Стратегия).
    """
    # 1. Получение клиента биржи (Singleton из контейнера)
    client = container.get_exchange_client(db_config.exchange)

    # 2. Сборка чистого конфигурационного объекта
    pydantic_config = _assemble_config(db_config)

    # 3. Инстанцирование стратегии
    # Стратегия получает уже готовый config и не знает о базе данных
    StrategyClass = AVAILABLE_STRATEGIES[pydantic_config.strategy_name]

    strategy = StrategyClass(
        events_queue=queue.Queue(),
        config=pydantic_config
    )

    # 4. Инициализация провайдера данных
    # Провайдеру нужны требования к индикаторам, которые стратегия сформировала при инициализации
    feed = LiveDataProvider(
        client=client,
        exchange=pydantic_config.exchange,
        instrument=pydantic_config.instrument,
        interval=pydantic_config.interval,
        feature_engine=container.feature_engine, # Singleton
        required_indicators=strategy.required_indicators
    )

    return feed, strategy


async def _async_main() -> None:
    """
    Главная асинхронная точка входа (Bootstrapper).

    1. Инициализирует глобальные сервисы (BotManager).
    2. Настраивает обработчики сигналов (Pipeline: Strategy -> Telegram/DB/Console).
    3. Запускает оркестратор движка.
    4. Обеспечивает Graceful Shutdown при получении SIGINT/SIGTERM.
    """
    logger.info("Запуск Live Signal Monitor...")

    # Получение глобальных сервисов
    bot_manager = container.bot_manager

    # Настройка цепочки обработки сигналов (Signal Handlers)
    # Сигнал от стратегии будет передан всем этим обработчикам
    signal_handlers = [
        TelegramSignalSender(bot_manager),  # Отправка в Telegram
        DBSignalLogger(),                   # Запись в историю БД
        ConsoleSignalViewer()               # Красивый вывод в терминал
    ]

    # Инициализация движка с обработчиками
    engine = SignalEngine(handlers=signal_handlers)

    tasks = []
    try:
        # --- Запуск фоновых задач ---

        # 1. Telegram Polling (Bot Manager)
        tasks.append(asyncio.create_task(bot_manager.start()))

        # 2. Торговый Движок (Orchestrator Loop)
        # Передаем функции загрузки и фабрики, чтобы движок сам управлял циклом
        tasks.append(asyncio.create_task(engine.run_orchestrator(
            config_loader_func=_config_loader,
            pair_builder_func=_pair_builder
        )))

        # --- Обработка сигналов ОС (Shutdown) ---
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def signal_handler(*args):
            logger.warning("🛑 Получен сигнал остановки. Завершение работы...")
            shutdown_event.set()

        # Регистрация обработчиков для Linux/Mac (на Windows работает ограниченно)
        if sys.platform != "win32":
            loop.add_signal_handler(signal.SIGTERM, signal_handler)
            loop.add_signal_handler(signal.SIGINT, signal_handler)

        # Ожидание сигнала остановки (или падения задач)
        # Мы используем wait, чтобы среагировать либо на сигнал выхода, либо на краш одной из задач
        done, pending = await asyncio.wait(
            tasks + [asyncio.create_task(shutdown_event.wait())],
            return_when=asyncio.FIRST_COMPLETED
        )

        # Если мы здесь, значит либо нажали Ctrl+C, либо одна из главных задач упала
        for task in done:
            if task.exception():
                logger.error(f"Критическая ошибка в фоновой задаче: {task.exception()}", exc_info=task.exception())

    except asyncio.CancelledError:
        logger.info("Main task cancelled.")

    finally:
        # --- Graceful Shutdown Procedure ---
        logger.info("Остановка всех сервисов...")

        await engine.stop() # Остановка стратегий

        # Отмена оставшихся задач
        for t in tasks:
            if not t.done():
                t.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Закрытие пула соединений с БД
        from app.infrastructure.database.session import engine as db_engine
        await db_engine.dispose()

        logger.info("Система остановлена корректно. Bye!")


def run_live_monitor_flow(settings: dict = None) -> None:
    """
    Синхронная точка входа для CLI/Launcher.

    Настраивает логирование и запускает AsyncIO Loop.
    Аргумент settings здесь не используется, так как конфигурация Live берется из БД,
    но он оставлен для унификации интерфейса раннеров.
    """
    setup_global_logging()

    # Windows-specific fix для SelectorEventLoop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        # Перехват Ctrl+C на уровне системы, если он прошел мимо asyncio
        pass