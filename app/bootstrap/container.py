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

    def get_exchange_client(self, exchange: str) -> Any:
        """
        Фабричный метод для получения клиента биржи.
        Всегда возвращает клиента для работы с реальными данными.

        Args:
            exchange (str): Название биржи (tinkoff, bybit).

        Returns:
            Union[TinkoffHandler, BybitHandler]: Клиент биржи.

        Raises:
            ValueError: Если запрошена неизвестная биржа.
        """
        key = exchange

        if key in self._exchange_clients:
            return self._exchange_clients[key]

        logger.info(f"Container: Initializing exchange client for {key}...")

        client = None
        if exchange == ExchangeType.TINKOFF:
            client = TinkoffHandler()  # Без аргументов
        elif exchange == ExchangeType.BYBIT:
            client = BybitHandler()  # Без аргументов
        else:
            raise ValueError(f"Unknown exchange: {exchange}")

        self._exchange_clients[key] = client
        return client


# Глобальный инстанс контейнера.
# Импортируя этот объект, другие модули получают доступ ко всем сервисам.
container = Container()