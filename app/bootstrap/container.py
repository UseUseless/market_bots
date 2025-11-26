from app.infrastructure.database.session import async_session_factory
from app.core.event_bus import SignalBus

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.event_bus import SignalBus
    from app.adapters.telegram.manager import BotManager
    from app.infrastructure.exchanges.tinkoff import TinkoffHandler
    from app.infrastructure.exchanges.bybit import BybitHandler


class Container:
    """
    DI Container: Центральная точка сборки приложения.
    Гарантирует, что Шина, Бот и Клиенты бирж существуют в единственном экземпляре.
    """

    def __init__(self):
        self._bus = None
        self._bot_manager = None
        self._tinkoff_client = None
        self._bybit_client = None

    @property
    def db_session_factory(self):
        """Фабрика сессий БД (SQLAlchemy)."""
        return async_session_factory

    @property
    def bus(self) -> "SignalBus":
        """Шина событий (Singleton)."""
        if not self._bus:
            from app.core.event_bus import SignalBus
            self._bus = SignalBus()
        return self._bus

    @property
    def bot_manager(self) -> "BotManager":
        """Менеджер Телеграм-ботов (Singleton)."""
        if not self._bot_manager:
            from app.adapters.telegram.manager import BotManager
            # Передаем фабрику сессий, которая нужна боту для работы с БД
            self._bot_manager = BotManager(self.db_session_factory)
        return self._bot_manager

    def get_exchange_client(self, exchange: str, mode: str = "SANDBOX"):
        """
        Фабрика клиентов бирж.
        Для Bybit/Tinkoff создаем один инстанс на приложение.
        """
        if exchange == "tinkoff":
            if not self._tinkoff_client:
                from app.infrastructure.exchanges.tinkoff import TinkoffHandler
                self._tinkoff_client = TinkoffHandler(trade_mode=mode)
            return self._tinkoff_client

        elif exchange == "bybit":
            if not self._bybit_client:
                from app.infrastructure.exchanges.bybit import BybitHandler
                self._bybit_client = BybitHandler(trade_mode=mode)
            return self._bybit_client

        else:
            raise ValueError(f"Unknown exchange: {exchange}")


# Глобальный экземпляр контейнера
container = Container()