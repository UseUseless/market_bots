import asyncio
import queue
import logging
from typing import Tuple, Any

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


async def _config_loader() -> list[StrategyConfig]:
    """
    Callback: Читает активные стратегии из БД.
    """
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        # Получаем конфиги вместе с привязанными ботами (eager load)
        configs = await repo.get_active_strategies()
        return configs


async def _pair_builder(config: StrategyConfig) -> Tuple[UnifiedDataFeed, Any]:
    """
    Callback: Фабрика для создания Feed и Strategy.
    Использует глобальный Container для получения зависимостей.
    """

    # 1. Определяем режим работы клиента биржи (согласно твоей исходной логике)
    # Tinkoff -> SANDBOX, Bybit -> REAL
    trade_mode = "SANDBOX" if config.exchange == ExchangeType.TINKOFF else "REAL"

    # Получаем клиент из контейнера (он сам разберется с кэшированием)
    client = container.get_exchange_client(config.exchange, mode=trade_mode)

    # 2. Ищем класс стратегии
    StrategyClass = AVAILABLE_STRATEGIES.get(config.strategy_name)
    if not StrategyClass:
        raise ValueError(f"Strategy class '{config.strategy_name}' not found")

    # 3. Собираем параметры
    strategy_params = StrategyClass.get_default_params()
    if config.parameters:
        strategy_params.update(config.parameters)

    # 4. Создаем Pydantic модель конфига (валидация данных из БД)
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
    # ВАЖНО: Берем feature_engine из контейнера (Singleton)
    strategy = StrategyClass(
        events_queue=queue.Queue(),
        feature_engine=container.feature_engine,
        config=pydantic_config
    )
    strategy.name = config.strategy_name

    # 6. Инициализируем поток данных (Feed)
    # Feed тоже использует feature_engine из контейнера
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
    Запускает сервисы и оркестратор.
    """
    logger.info("Запуск Live Monitor Orchestrator...")

    # 1. Получаем сервисы из контейнера
    bus = container.bus
    bot_manager = container.bot_manager

    # Движок управляет запуском/остановкой стратегий
    engine = SignalEngine(bus)

    # 2. Инициализируем адаптеры (слушатели шины)
    console_adapter = ConsoleAdapter(bus)
    db_logger = DBLoggerAdapter(bus)
    telegram_bridge = TelegramBridge(bus, bot_manager)

    tasks = []
    try:
        # --- Запуск фоновых задач ---

        # 1. Слушатели событий (сигналы -> консоль/бд/телеграм)
        tasks.append(asyncio.create_task(console_adapter.start()))
        tasks.append(asyncio.create_task(db_logger.start()))
        tasks.append(asyncio.create_task(telegram_bridge.start()))

        # 2. Менеджер ботов (polling telegram)
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
        # Корректное завершение всех задач
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def run_live_monitor_flow(settings: dict = None):
    """
    Синхронная обертка для запуска из лаунчера.
    """
    setup_global_logging()
    try:
        # Для Windows иногда требуется специфичная политика цикла событий
        # if sys.platform == 'win32': ... (обычно уже настроено в лаунчере, но имей в виду)
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")