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
import logging
import queue
import signal
from typing import Tuple, Any, List

# Импорты БД
from app.shared.schemas import StrategyConfigModel
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import ConfigRepository, PortfolioRepository
from app.infrastructure.database.models import StrategyConfig

from app.bootstrap.container import container
from app.core.engine.live.loop import SignalEngine
from app.infrastructure.feeds.unified import UnifiedDataFeed
from app.adapters.cli.signal_viewer import ConsoleAdapter
from app.infrastructure.database.signal_logger import DBLoggerAdapter
from app.adapters.telegram.publisher import TelegramBridge

from app.strategies import AVAILABLE_STRATEGIES
from app.shared.logging_setup import setup_global_logging
# Импорт стейта
from app.core.portfolio.state import PortfolioState
from app.shared.config import config as app_config

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
        configs = await repo.get_active_strategies()
        return configs

async def _pair_builder(config: StrategyConfig) -> Tuple[UnifiedDataFeed, Any, PortfolioState]:
    """
    Функция-фабрика (Factory Callback).

    Создает и настраивает экземпляры `Strategy` и `UnifiedDataFeed` на основе
    конфигурации из БД.

    Args:
        config (StrategyConfig): ORM-объект конфигурации стратегии.

    Returns:
        Tuple[UnifiedDataFeed, BaseStrategy, PortfolioState]: Готовая пара для запуска в движке.

    Raises:
        ValueError: Если указанная в конфиге стратегия не найдена в коде.
    """
    # 1. Получаем клиент
    client = container.get_exchange_client(config.exchange)

    # 2. Инициализация стратегии
    StrategyClass = AVAILABLE_STRATEGIES.get(config.strategy_name)
    if not StrategyClass:
        raise ValueError(f"Strategy class '{config.strategy_name}' not found")

    strategy_params = StrategyClass.get_default_params()
    if config.parameters:
        strategy_params.update(config.parameters)

    pydantic_config = StrategyConfigModel(
        strategy_name=config.strategy_name,
        instrument=config.instrument,
        exchange=config.exchange,
        interval=config.interval,
        params=strategy_params,
        risk_manager_type=config.risk_manager_type or "FIXED",
        risk_manager_params={}
    )

    strategy = StrategyClass(
        events_queue=queue.Queue(),
        feature_engine=container.feature_engine,
        config=pydantic_config
    )
    strategy.name = config.strategy_name

    # 3. Инициализация фида
    feed = UnifiedDataFeed(
        client=client,
        exchange=config.exchange,
        instrument=config.instrument,
        interval=config.interval,
        feature_engine=container.feature_engine,
        required_indicators=strategy.required_indicators
    )

    # 4. ВОССТАНОВЛЕНИЕ СОСТОЯНИЯ (State Recovery)
    # Загружаем из БД или создаем новое
    async with async_session_factory() as session:
        repo = PortfolioRepository(session)

        # Определяем стартовый капитал (из конфига стратегии или глобальный дефолт)
        initial_cap = strategy_params.get("initial_capital", app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"])

        portfolio_state = await repo.load_portfolio_state(
            config_id=config.id,
            initial_capital=float(initial_cap)
        )

        if portfolio_state.positions:
            logger.info(
                f"♻️ Восстановлено состояние для {config.instrument}: {len(portfolio_state.positions)} позиций.")

    return feed, strategy, portfolio_state


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

        # --- Обработка сигналов (Graceful Shutdown) ---
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
            logger.warning("⚠️ Signal handlers not supported on this platform. Use Ctrl+C/Kill.")

        logger.info("🚀 Система запущена. Ожидание событий...")

        # Используем return_exceptions=True, чтобы падение одной задачи не крашило всё
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
        # Ждем фактической отмены, игнорируя ошибки отмены
        await asyncio.gather(*tasks, return_exceptions=True)

        # Закрываем соединения с БД для предотвращения ошибок при повторном запуске
        from app.infrastructure.database.session import engine as db_engine
        await db_engine.dispose()
        logger.info("Database connections closed.")

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
        # Этот блок ловит Ctrl+C до того, как loop запустится или если asyncio.run завершится.
        # Основная обработка внутри _async_main через signal_handler.
        print("\nОстановлено пользователем.")