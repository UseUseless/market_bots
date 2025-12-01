"""
Контейнер зависимостей (Dependency Injection Container).

Этот модуль отвечает за сборку приложения ("Wiring"). Он создает, настраивает
и предоставляет доступ к глобальным сервисам-синглтонам. Это позволяет
избежать глобальных переменных, разбросанных по коду, и упрощает управление
зависимостями.

Основные компоненты:
- **Core Services:** Шина событий, движок индикаторов.
- **Adapters:** Менеджер Телеграм-ботов.
- **Infrastructure:** Клиенты бирж (с кэшированием соединений).
"""

import logging
from typing import Dict, Optional, Any

from app.infrastructure.database.session import async_session_factory
from app.core.calculations.indicators import FeatureEngine
from app.core.event_bus import SignalBus
from app.adapters.telegram.manager import BotManager
from app.infrastructure.exchanges.tinkoff import TinkoffHandler
from app.infrastructure.exchanges.bybit import BybitHandler
from app.shared.config import config
from app.shared.primitives import ExchangeType

logger = logging.getLogger(__name__)


class Container:
    """
    DI Контейнер для управления сервисами приложения.

    Реализует паттерн "Ленивая инициализация" (Lazy Initialization):
    сервисы создаются только при первом обращении к ним.
    """

    def __init__(self):
        # Кэши для синглтонов (инициализируются None)
        self._bus: Optional[SignalBus] = None
        self._bot_manager: Optional[BotManager] = None
        self._feature_engine: Optional[FeatureEngine] = None

        # Кэш клиентов бирж: { "exchange_mode": ClientInstance }
        # Позволяет переиспользовать одно TCP-соединение для множества стратегий.
        self._exchange_clients: Dict[str, Any] = {}

    @property
    def settings(self):
        """
        Доступ к глобальным настройкам приложения.

        Returns:
            AppConfig: Объект конфигурации.
        """
        return config

    @property
    def db_session_factory(self):
        """
        Фабрика асинхронных сессий SQLAlchemy.
        Используется для создания подключений к БД внутри сервисов.
        """
        return async_session_factory

    @property
    def bus(self) -> SignalBus:
        """
        Глобальная шина событий (Event Bus).

        Используется для асинхронного обмена сообщениями между:
        - Поставщиками данных (Feeds)
        - Стратегиями
        - Исполнителями ордеров (Execution)
        - Логгерами и Телеграмом
        """
        if not self._bus:
            self._bus = SignalBus()
            logger.debug("Container: SignalBus initialized.")
        return self._bus

    @property
    def feature_engine(self) -> FeatureEngine:
        """
        Сервис расчета технических индикаторов.
        Содержит логику Pandas-TA и оптимизации вычислений.
        """
        if not self._feature_engine:
            self._feature_engine = FeatureEngine()
            logger.debug("Container: FeatureEngine initialized.")
        return self._feature_engine

    @property
    def bot_manager(self) -> BotManager:
        """
        Менеджер Телеграм-ботов.
        Управляет запуском/остановкой поллинга и рассылкой сообщений.
        """
        if not self._bot_manager:
            self._bot_manager = BotManager(self.db_session_factory)
            logger.debug("Container: BotManager initialized.")
        return self._bot_manager

    def get_exchange_client(self, exchange: str, mode: str = "SANDBOX") -> Any:
        """
        Фабричный метод для получения клиента биржи.

        Реализует паттерн Flyweight (Приспособленец): если клиент с такими
        параметрами уже был создан, возвращает существующий экземпляр.

        Args:
            exchange (str): Название биржи (tinkoff, bybit).
            mode (str): Режим торгов (SANDBOX, REAL).

        Returns:
            Union[TinkoffHandler, BybitHandler]: Клиент биржи.

        Raises:
            ValueError: Если запрошена неизвестная биржа.
        """
        key = f"{exchange}_{mode}"

        # Если клиент уже есть в кэше — возвращаем его
        if key in self._exchange_clients:
            return self._exchange_clients[key]

        logger.info(f"Container: Initializing exchange client for {key}...")

        client = None
        if exchange == ExchangeType.TINKOFF:
            client = TinkoffHandler(trade_mode=mode)
        elif exchange == ExchangeType.BYBIT:
            client = BybitHandler(trade_mode=mode)
        else:
            raise ValueError(f"Unknown exchange: {exchange}")

        # Сохраняем в кэш
        self._exchange_clients[key] = client
        return client


# Глобальный инстанс контейнера.
# Импортируя этот объект, другие модули получают доступ ко всем сервисам.
container = Container()