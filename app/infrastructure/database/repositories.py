"""
Репозитории для доступа к данным (Data Access Layer).

Этот модуль реализует паттерн Repository для абстрагирования работы с базой данных.
Каждый класс отвечает за CRUD-операции для конкретной группы сущностей,
скрывая детали реализации SQLAlchemy и SQL-запросов от бизнес-логики.
"""

from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.database.models import (
    BotInstance, StrategyConfig, TelegramSubscriber, SignalLog
)
from app.shared.events import SignalEvent


class ConfigRepository:
    """
    Репозиторий для управления конфигурациями системы (Боты и Стратегии).

    Отвечает за создание и получение настроек, необходимых для запуска
    торгового ядра и привязки стратегий к ботам уведомлений.
    """

    def __init__(self, session: AsyncSession):
        """
        Инициализирует репозиторий.

        Args:
            session (AsyncSession): Активная асинхронная сессия базы данных.
        """
        self.session = session

    async def create_bot(self, name: str, token: str) -> BotInstance:
        """
        Создает новую запись Telegram-бота.

        Args:
            name (str): Внутреннее уникальное имя бота.
            token (str): API токен от BotFather.

        Returns:
            BotInstance: Созданный объект бота с заполненным ID.
        """
        bot = BotInstance(name=name, token=token)
        self.session.add(bot)

        # Фиксация транзакции и обновление объекта (получение присвоенного ID)
        await self.session.commit()
        await self.session.refresh(bot)
        return bot

    async def add_strategy_config(self, config_data: dict) -> StrategyConfig:
        """
        Сохраняет новую конфигурацию стратегии.

        Args:
            config_data (dict): Словарь параметров (exchange, instrument, params и т.д.).

        Returns:
            StrategyConfig: Созданный объект конфигурации.
        """
        config = StrategyConfig(**config_data)
        self.session.add(config)
        await self.session.commit()
        await self.session.refresh(config)
        return config

    async def get_active_strategies(self) -> List[StrategyConfig]:
        """
        Получает список всех активных стратегий для запуска в Live-режиме.

        Включает "жадную" загрузку (Eager Loading) связанной сущности `bot`.
        Это необходимо, так как в асинхронной SQLAlchemy доступ к связанным
        атрибутам (Lazy Loading) после закрытия сессии невозможен и вызывает ошибку.

        Returns:
            List[StrategyConfig]: Список конфигураций с подгруженными ботами.
        """
        query = (
            select(StrategyConfig)
            .options(selectinload(StrategyConfig.bot))  # Подгружаем бота сразу
            .where(StrategyConfig.is_active == True)
        )
        result = await self.session.execute(query)
        return result.scalars().all()


class SignalRepository:
    """
    Репозиторий для логирования торговых событий (Append-only Log).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_signal(self, event: SignalEvent) -> None:
        """
        Сохраняет событие сигнала в базу данных.

        Используется для построения истории сигналов в Dashboard и аналитики.

        Args:
            event (SignalEvent): Доменный объект события.
        """
        # Маппинг события на ORM-модель
        signal = SignalLog(
            timestamp=event.timestamp,
            exchange="unknown",  # Биржа может быть добавлена в событие позже
            instrument=event.instrument,
            strategy_name=event.strategy_name,
            direction=event.direction,
            price=event.price
        )
        self.session.add(signal)
        await self.session.commit()


class BotRepository:
    """
    Репозиторий для работы с Telegram-сущностями (Боты, Подписчики).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active_bots(self) -> List[BotInstance]:
        """
        Возвращает список всех включенных ботов.

        Returns:
            List[BotInstance]: Список ботов с флагом is_active=True.
        """
        query = select(BotInstance).where(BotInstance.is_active == True)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_bot_by_token(self, token: str) -> Optional[BotInstance]:
        """
        Ищет бота по токену API.

        Args:
            token (str): Токен.

        Returns:
            Optional[BotInstance]: Найденный бот или None.
        """
        query = select(BotInstance).where(BotInstance.token == token)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def register_subscriber(self, bot_id: int, chat_id: int, username: str = None) -> bool:
        """
        Управляет подпиской пользователя на бота (Upsert logic).

        Обрабатывает три сценария:
        1. Пользователя нет -> Создает новую запись.
        2. Пользователь был, но отписался (is_active=False) -> Активирует обратно.
        3. Пользователь активен -> Ничего не делает.

        Args:
            bot_id (int): ID бота в нашей БД (не Telegram ID бота).
            chat_id (int): Telegram ID пользователя (или чата).
            username (str, optional): Юзернейм пользователя.

        Returns:
            bool: True, если статус пользователя изменился на "Активен" (новый или вернувшийся).
                  False, если пользователь уже был активен.
        """
        query = select(TelegramSubscriber).where(
            TelegramSubscriber.bot_id == bot_id,
            TelegramSubscriber.chat_id == chat_id
        )
        result = await self.session.execute(query)
        sub = result.scalar_one_or_none()

        # Сценарий 1: Новый подписчик
        if not sub:
            sub = TelegramSubscriber(bot_id=bot_id, chat_id=chat_id, username=username)
            self.session.add(sub)
            await self.session.commit()
            return True

        # Сценарий 2: Вернувшийся подписчик
        if not sub.is_active:
            sub.is_active = True
            await self.session.commit()
            return True

        # Сценарий 3: Уже подписан
        return False

    async def get_subscribers_for_strategy(self, strategy_config_id: int) -> List[int]:
        """
        Находит всех получателей сигналов для конкретной стратегии.

        Алгоритм:
        1. Находит конфиг стратегии по ID.
        2. Определяет, к какому боту привязана стратегия.
        3. Находит всех активных подписчиков этого бота.

        Args:
            strategy_config_id (int): ID конфигурации стратегии.

        Returns:
            List[int]: Список Telegram Chat ID для рассылки.
        """
        # 1. Получаем конфиг стратегии
        config = await self.session.get(StrategyConfig, strategy_config_id)
        if not config or not config.bot_id:
            return []

        # 2. Выбираем подписчиков соответствующего бота
        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == config.bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_subscribers_for_bot(self, bot_id: int) -> List[int]:
        """
        Получает список ID всех активных подписчиков конкретного бота.
        Используется для системных рассылок (Broadcast).

        Args:
            bot_id (int): ID бота.

        Returns:
            List[int]: Список Chat ID.
        """
        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()