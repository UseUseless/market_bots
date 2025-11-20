import asyncio
import queue
import logging
from typing import Dict, Any, Tuple

# DB & Repos
from app.storage.database import async_session_factory
from app.storage.repositories import ConfigRepository
from app.storage.models import StrategyConfig

# Core Components
from app.core.services.feature_engine import FeatureEngine
from app.core.data.feeds.unified_feed import UnifiedDataFeed
from app.live.bus.signal_bus import SignalBus
from app.live.engine.signal import SignalEngine
from app.live.adapters.console_adapter import ConsoleAdapter
from app.live.adapters.db_logger import DBLoggerAdapter
from app.live.adapters.telegram_bridge import TelegramBridge
from app.bots.manager import BotManager

# Clients & Strategies
from app.utils.clients.tinkoff import TinkoffHandler
from app.utils.clients.bybit import BybitHandler
from app.strategies import AVAILABLE_STRATEGIES
from app.utils.logging_setup import setup_global_logging

logger = logging.getLogger(__name__)


class LiveSystemContext:
    """
    Контейнер для хранения долгоживущих объектов (клиентов, менеджеров),
    которые нужны фабрике стратегий.
    """

    def __init__(self):
        self.clients = {}
        self.feature_engine = FeatureEngine()

    def get_client(self, exchange: str):
        if exchange in self.clients:
            return self.clients[exchange]

        logger.info(f"Initializing shared client for {exchange}...")
        if exchange == 'tinkoff':
            client = TinkoffHandler(trade_mode="SANDBOX")
        elif exchange == 'bybit':
            client = BybitHandler(trade_mode="REAL")
        else:
            raise ValueError(f"Unknown exchange {exchange}")

        self.clients[exchange] = client
        return client


async def _config_loader():
    """Callback: Читает активные стратегии из БД."""
    async with async_session_factory() as session:
        repo = ConfigRepository(session)
        configs = await repo.get_active_strategies()
        # Важно: SQLAlchemy объекты привязаны к сессии.
        # Чтобы использовать их вне сессии (после закрытия),
        # иногда нужно делать expunge или загружать жадно.
        # Но для простых полей (id, name) это обычно работает, пока мы не лезем в lazy-связи.
        return configs


async def _pair_builder(config: StrategyConfig, context: LiveSystemContext):
    """Callback: Фабрика для создания Feed и Strategy."""
    client = context.get_client(config.exchange)

    StrategyClass = AVAILABLE_STRATEGIES.get(config.strategy_name)
    if not StrategyClass:
        raise ValueError(f"Strategy class '{config.strategy_name}' not found")

    strategy_params = StrategyClass.get_default_params()
    if config.parameters:
        strategy_params.update(config.parameters)

    strategy = StrategyClass(
        events_queue=queue.Queue(),  # Dummy
        instrument=config.instrument,
        params=strategy_params,
        feature_engine=context.feature_engine,
        risk_manager_type=config.risk_manager_type
    )

    strategy.name = config.strategy_name

    feed = UnifiedDataFeed(
        client=client,
        exchange=config.exchange,
        instrument=config.instrument,
        interval=config.interval,
        feature_engine=context.feature_engine,
        required_indicators=strategy.required_indicators
    )

    return feed, strategy


async def _async_main():
    # 1. Инфраструктура
    bus = SignalBus()
    engine = SignalEngine(bus)
    context = LiveSystemContext()

    # 2. Адаптеры
    console_adapter = ConsoleAdapter(bus)
    db_logger = DBLoggerAdapter(bus)

    bot_manager = BotManager(async_session_factory)
    telegram_bridge = TelegramBridge(bus, bot_manager)

    # 3. Привязка функций для движка
    # Мы используем lambda или partial, чтобы пробросить context
    build_func = lambda cfg: _pair_builder(cfg, context)

    tasks = []
    try:
        # Запуск слушателей
        tasks.append(asyncio.create_task(console_adapter.start()))
        tasks.append(asyncio.create_task(db_logger.start()))
        tasks.append(asyncio.create_task(telegram_bridge.start()))
        tasks.append(asyncio.create_task(bot_manager.start()))

        # Запуск ОРКЕСТРАТОРА (он теперь главный по стратегиям)
        tasks.append(asyncio.create_task(engine.run_orchestrator(
            config_loader_func=_config_loader,
            pair_builder_func=build_func
        )))

        logger.info("🚀 Live Monitor c Hot Reload запущен! Управляйте стратегиями через Дэшборд.")

        await asyncio.gather(*tasks)

    except asyncio.CancelledError:
        logger.info("System stopping...")
        await engine.stop()
    finally:
        for t in tasks: t.cancel()


def run_live_monitor_flow(settings: Dict[str, Any] = None):
    setup_global_logging()
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")