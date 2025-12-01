"""
Оркестратор запуска Live-режима (Live Monitor Orchestrator).

Этот модуль является точкой входа для запуска системы в режиме реального времени.
Он отвечает за "сборку" приложения (Application Assembly): инициализацию
глобальных сервисов, настройку адаптеров ввода-вывода и запуск главного
асинхронного цикла.

Архитектура:
    Модуль связывает инфраструктурный слой (БД, Биржи) с ядром (SignalEngine),
    используя Dependency Injection контейнер.
"""

import asyncio
import queue
import logging
from typing import Tuple, Any, List

from app.shared.schemas import StrategyConfigModel
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import ConfigRepository
from app.infrastructure.database.models import StrategyConfig

from app.bootstrap.container import container
from app.core.engine.live.loop import SignalEngine
from app.infrastructure.feeds.unified import UnifiedDataFeed
from app.adapters.cli.signal_viewer import ConsoleAdapter
from app.infrastructure.database.signal_logger import DBLoggerAdapter
from app.adapters.telegram.publisher import TelegramBridge

from app.strategies import AVAILABLE_STRATEGIES
from app.shared.logging_setup import setup_global_logging
from app.shared.primitives import ExchangeType

logger = logging.getLogger(__name__)


async def _config_loader() -> List[StrategyConfig]:
    """
    Функция-поставщик конфигураций (Callback).

    Передается в `SignalEngine`. При каждом цикле "Hot Reload" движок вызывает
    эту функцию, чтобы получить актуальный список активных стратегий из БД.

    Returns:
        List[StrategyConfig]: Список ORM-объектов конфигурации.
    """
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        # Получаем конфиги вместе с привязанными ботами (eager load)
        configs = await repo.get_active_strategies()
        return configs


async def _pair_builder(config: StrategyConfig) -> Tuple[UnifiedDataFeed, Any]:
    """
    Функция-фабрика (Factory Callback).

    Создает и настраивает экземпляры `Strategy` и `UnifiedDataFeed` на основе
    конфигурации из БД.

    Args:
        config (StrategyConfig): ORM-объект конфигурации стратегии.

    Returns:
        Tuple[UnifiedDataFeed, BaseStrategy]: Готовая пара для запуска в движке.

    Raises:
        ValueError: Если указанная в конфиге стратегия не найдена в коде.
    """
    # 1. Определяем режим работы клиента биржи.
    # Логика: Tinkoff используем в Sandbox (безопасно), Bybit — Real (для данных).
    # В будущем это можно вынести в настройки самой стратегии.
    trade_mode = "SANDBOX" if config.exchange == ExchangeType.TINKOFF else "REAL"

    # Получаем клиент из DI-контейнера (он кэшируется внутри контейнера)
    client = container.get_exchange_client(config.exchange, mode=trade_mode)

    # 2. Ищем класс стратегии в реестре
    StrategyClass = AVAILABLE_STRATEGIES.get(config.strategy_name)
    if not StrategyClass:
        raise ValueError(f"Strategy class '{config.strategy_name}' not found")

    # 3. Собираем параметры (дефолтные + переопределенные в БД)
    strategy_params = StrategyClass.get_default_params()
    if config.parameters:
        strategy_params.update(config.parameters)

    # 4. Создаем Pydantic модель для строгой валидации
    pydantic_config = StrategyConfigModel(
        strategy_name=config.strategy_name,
        instrument=config.instrument,
        exchange=config.exchange,
        interval=config.interval,
        params=strategy_params,
        risk_manager_type=config.risk_manager_type or "FIXED",
        risk_manager_params={}
    )

    # 5. Инициализируем стратегию
    # ВАЖНО: FeatureEngine берется из контейнера (Singleton), чтобы не плодить инстансы
    strategy = StrategyClass(
        events_queue=queue.Queue(),
        feature_engine=container.feature_engine,
        config=pydantic_config
    )
    strategy.name = config.strategy_name

    # 6. Инициализируем поток данных (Feed)
    feed = UnifiedDataFeed(
        client=client,
        exchange=config.exchange,
        instrument=config.instrument,
        interval=config.interval,
        feature_engine=container.feature_engine,
        required_indicators=strategy.required_indicators
    )

    return feed, strategy


async def _async_main():
    """
    Главная асинхронная точка входа.

    Инициализирует компоненты системы, запускает фоновые задачи (Tasks)
    и управляет их жизненным циклом.
    """
    logger.info("Запуск Live Monitor Orchestrator...")

    # 1. Получаем глобальные сервисы из контейнера
    bus = container.bus
    bot_manager = container.bot_manager

    # Инициализируем движок, который будет управлять стратегиями
    engine = SignalEngine(bus)

    # 2. Инициализируем адаптеры (слушатели шины событий)
    console_adapter = ConsoleAdapter(bus)       # Вывод в консоль
    db_logger = DBLoggerAdapter(bus)            # Запись в БД
    telegram_bridge = TelegramBridge(bus, bot_manager) # Отправка в Telegram

    tasks = []
    try:
        # --- Запуск фоновых задач ---

        # 1. Слушатели событий
        tasks.append(asyncio.create_task(console_adapter.start()))
        tasks.append(asyncio.create_task(db_logger.start()))
        tasks.append(asyncio.create_task(telegram_bridge.start()))

        # 2. Менеджер ботов (Polling Telegram API)
        tasks.append(asyncio.create_task(bot_manager.start()))

        # 3. ОРКЕСТРАТОР (Главный цикл управления стратегиями)
        # Передаем ему функции-коллбэки для работы с БД и создания объектов
        tasks.append(asyncio.create_task(engine.run_orchestrator(
            config_loader_func=_config_loader,
            pair_builder_func=_pair_builder
        )))

        logger.info("🚀 Система запущена. Ожидание событий...")

        # Ожидаем выполнения всех задач (бесконечно, пока не будет ошибки или отмены)
        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        logger.info("Остановка системы (KeyboardInterrupt)...")
        await engine.stop()
    except Exception as e:
        logger.critical(f"Критическая ошибка в main loop: {e}", exc_info=True)
    finally:
        # Корректное завершение всех задач при выходе
        logger.info("Завершение всех фоновых задач...")
        for t in tasks:
            t.cancel()
        # Ждем фактической отмены, игнорируя ошибки отмены
        await asyncio.gather(*tasks, return_exceptions=True)


def run_live_monitor_flow(settings: dict = None):
    """
    Синхронная обертка для запуска из лаунчера.

    Настраивает логирование и запускает `asyncio` цикл.

    Args:
        settings (dict, optional): Настройки запуска (пока не используются,
                                   так как конфиг берется из БД).
    """
    setup_global_logging()
    try:
        # Запуск асинхронного ядра
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")