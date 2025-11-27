import logging
from typing import Dict, Optional

from app.infrastructure.database.session import async_session_factory
from app.core.calculations.indicators import FeatureEngine
from app.core.event_bus import SignalBus
from app.adapters.telegram.manager import BotManager
from app.infrastructure.exchanges.tinkoff import TinkoffHandler
from app.infrastructure.exchanges.bybit import BybitHandler
from app.shared.config import config

logger = logging.getLogger(__name__)


class Container:
    """
    DI Container: Отвечает за создание и хранение singleton-сервисов.
    """

    def __init__(self):
        # Кэши для синглтонов
        self._bus: Optional[SignalBus] = None
        self._bot_manager: Optional[BotManager] = None
        self._feature_engine: Optional[FeatureEngine] = None

        # Кэш клиентов бирж (чтобы не пересоздавать коннекты)
        self._exchange_clients: Dict[str, object] = {}

    @property
    def settings(self):
        """Централизованный доступ к настройкам."""
        return config

    @property
    def db_session_factory(self):
        """Фабрика сессий SQLAlchemy."""
        return async_session_factory

    @property
    def bus(self) -> SignalBus:
        """Шина событий (Event Bus)."""
        if not self._bus:
            self._bus = SignalBus()
            logger.debug("Container: SignalBus initialized.")
        return self._bus

    @property
    def feature_engine(self) -> FeatureEngine:
        """Сервис расчета индикаторов."""
        if not self._feature_engine:
            self._feature_engine = FeatureEngine()
            logger.debug("Container: FeatureEngine initialized.")
        return self._feature_engine

    @property
    def bot_manager(self) -> BotManager:
        """Менеджер телеграм-ботов."""
        if not self._bot_manager:
            self._bot_manager = BotManager(self.db_session_factory)
            logger.debug("Container: BotManager initialized.")
        return self._bot_manager

    def get_exchange_client(self, exchange: str, mode: str = "SANDBOX"):
        """
        Фабрика клиентов бирж.
        """
        key = f"{exchange}_{mode}"
        if key in self._exchange_clients:
            return self._exchange_clients[key]

        logger.info(f"Container: Initializing exchange client for {key}...")

        client = None
        if exchange == "tinkoff":
            client = TinkoffHandler(trade_mode=mode)
        elif exchange == "bybit":
            client = BybitHandler(trade_mode=mode)
        else:
            raise ValueError(f"Unknown exchange: {exchange}")

        self._exchange_clients[key] = client
        return client


# Глобальный инстанс контейнера
container = Container()