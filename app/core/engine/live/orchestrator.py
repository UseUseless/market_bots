"""
Оркестратор запуска Live-режима (Live Monitor Orchestrator).

Этот модуль является точкой входа для запуска системы в режиме реального времени
(Signal Monitor). Он отвечает за "сборку" приложения (Application Assembly):
инициализацию глобальных сервисов, настройку обработчиков сигналов и запуск
главного асинхронного цикла.

Архитектура:
    Модуль связывает инфраструктурный слой (БД, Биржи) с ядром (SignalEngine),
    используя принципы Dependency Injection (DI) и Factory Pattern.

Особенности режима Monitor:
    В отличие от бэктеста, здесь отключена система управления портфелем (`PortfolioState`),
    симулятор ордеров и учет PnL. Система работает по упрощенной схеме:
    Data -> Strategy -> Signal -> Notification.
"""

import asyncio
import logging
import queue
import signal
from typing import Tuple, Any, List

# Импорты схем и БД
from app.shared.schemas import TradingConfig
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import ConfigRepository
from app.infrastructure.database.models import StrategyConfig

# Глобальные зависимости
from app.bootstrap.container import container
from app.core.engine.live.loop import SignalEngine
from app.infrastructure.feeds.live import LiveDataProvider

# Обработчики сигналов (Signal Handlers)
from app.adapters.cli.signal_viewer import ConsoleSignalViewer
from app.infrastructure.database.signal_logger import DBSignalLogger
from app.adapters.telegram.publisher import TelegramSignalSender

# Стратегии и утилиты
from app.strategies import AVAILABLE_STRATEGIES
from app.shared.logging_setup import setup_global_logging

logger = logging.getLogger(__name__)


async def _config_loader() -> List[StrategyConfig]:
    """
    Функция-поставщик конфигураций (Callback).

    Передается в `SignalEngine`. При каждом цикле "Hot Reload" движок вызывает
    эту функцию, чтобы получить актуальный список активных стратегий из БД.

    Returns:
        List[StrategyConfig]: Список ORM-объектов конфигурации активных стратегий.
    """
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        configs = await repo.get_active_strategies()
        return configs


async def _pair_builder(config: StrategyConfig) -> Tuple[LiveDataProvider, Any]:
    """
    Функция-фабрика (Factory Callback).

    Создает и настраивает экземпляры `Strategy` и `UnifiedDataProvider` на основе
    конфигурации из БД.

    Args:
        config (StrategyConfig): ORM-объект конфигурации стратегии.

    Returns:
        Tuple[LiveDataProvider, Any]: Кортеж (DataFeed, Strategy), готовый
        для запуска в движке.

    Raises:
        ValueError: Если указанная в конфиге стратегия не найдена в реестре.
    """
    # 1. Получаем клиент биржи из контейнера (Singleton)
    client = container.get_exchange_client(config.exchange)

    # 2. Инициализация класса стратегии
    StrategyClass = AVAILABLE_STRATEGIES.get(config.strategy_name)
    if not StrategyClass:
        raise ValueError(f"Strategy class '{config.strategy_name}' not found")

    # 3. Слияние параметров (Default + DB override)
    strategy_params = StrategyClass.get_default_params()
    if config.parameters:
        strategy_params.update(config.parameters)

    # 4. Создание валидированной модели конфигурации
    pydantic_config = TradingConfig(
        strategy_name=config.strategy_name,
        instrument=config.instrument,
        exchange=config.exchange,
        interval=config.interval,
        params=strategy_params,
        # Риск-менеджер здесь нужен только для того, чтобы стратегия знала,
        # какие индикаторы (напр. ATR) добавить в required_indicators.
        # Сами расчеты рисков и сайзинга в режиме Monitor отключены.
        risk_manager_type=config.risk_manager_type or "FIXED",
        risk_manager_params={}
    )

    # 5. Инстанцирование стратегии
    strategy = StrategyClass(
        events_queue=queue.Queue(),
        feature_engine=container.feature_engine,
        config=pydantic_config
    )
    strategy.name = config.strategy_name

    # 6. Инициализация фида данных
    feed = LiveDataProvider(
        client=client,
        exchange=config.exchange,
        instrument=config.instrument,
        interval=config.interval,
        feature_engine=container.feature_engine,
        required_indicators=strategy.required_indicators
    )

    # В режиме монитора мы НЕ загружаем PortfolioState из БД.
    return feed, strategy


async def _async_main() -> None:
    """
    Главная асинхронная точка входа (Wiring).

    Инициализирует компоненты системы, связывает их друг с другом,
    запускает фоновые задачи (Tasks) и управляет их жизненным циклом.
    """
    logger.info("Запуск Live Signal Monitor (Lightweight Mode)...")

    # 1. Получаем глобальные сервисы из контейнера
    bot_manager = container.bot_manager

    # 2. Инициализируем обработчики сигналов (Signal Handlers)
    # Используем Direct Composition для маршрутизации сигналов
    telegram_sender = TelegramSignalSender(bot_manager)  # Отправка в Telegram
    db_logger = DBSignalLogger()                         # Логирование в БД
    console_view = ConsoleSignalViewer()                 # Вывод в консоль

    signal_handlers = [telegram_sender, db_logger, console_view]

    # 3. Инициализируем движок
    engine = SignalEngine(handlers=signal_handlers)

    tasks = []
    try:
        # --- Запуск фоновых задач ---

        # Задача 1: Менеджер ботов (Polling Telegram API)
        tasks.append(asyncio.create_task(bot_manager.start()))

        # Задача 2: ОРКЕСТРАТОР (Главный цикл управления стратегиями)
        # Передаем функции-коллбэки для работы с БД и создания объектов
        tasks.append(asyncio.create_task(engine.run_orchestrator(
            config_loader_func=_config_loader,
            pair_builder_func=_pair_builder
        )))

        # --- Обработка сигналов остановки (Graceful Shutdown) ---
        loop = asyncio.get_running_loop()

        def signal_handler():
            logger.warning("🛑 Received shutdown signal (SIGTERM/SIGINT). Cancelling tasks...")
            for task in tasks:
                if not task.done():
                    task.cancel()

        # Регистрация обработчиков (try-except для совместимости с Windows)
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, signal_handler)
            logger.info("✅ Signal handlers registered (SIGTERM/SIGINT).")
        except NotImplementedError:
            logger.warning("⚠️ Signal handlers not supported on this platform. Use Ctrl+C to stop.")

        logger.info("🚀 Монитор сигналов запущен. Ожидание событий...")

        # Ожидание завершения задач
        # return_exceptions=True предотвращает немедленное падение всего приложения
        # при ошибке в одной из задач
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                logger.error(f"Task failed with error: {res}", exc_info=res)

    except asyncio.CancelledError:
        logger.info("Остановка системы (Main Task Cancelled)...")
        await engine.stop()
    except Exception as e:
        logger.critical(f"Критическая ошибка в main loop: {e}", exc_info=True)
    finally:
        # Корректное завершение всех задач при выходе
        logger.info("Завершение всех фоновых задач...")
        for t in tasks:
            if not t.done():
                t.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Закрываем соединения с БД
        from app.infrastructure.database.session import engine as db_engine
        await db_engine.dispose()
        logger.info("Database connections closed.")


def run_live_monitor_flow(settings: dict = None) -> None:
    """
    Синхронная обертка для запуска из лаунчера.

    Настраивает логирование и запускает `asyncio` цикл.

    Args:
        settings (dict, optional): Настройки запуска (не используются,
                                   конфиг берется из БД).
    """
    setup_global_logging()
    try:
        # Запуск асинхронного ядра
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")