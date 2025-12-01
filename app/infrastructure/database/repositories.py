"""
Репозитории для доступа к данным (Data Access Layer).

Этот модуль реализует паттерн Repository, предоставляя абстракцию над
прямыми запросами SQLAlchemy. Бизнес-логика использует эти классы
для создания, чтения, обновления и удаления записей в БД.
"""

from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.portfolio.state import PortfolioState
from app.shared.primitives import TradeDirection, Position
from app.core.interfaces import IPortfolioRepository
from app.infrastructure.database.models import (
    PortfolioDB,
    PositionDB,
    BotInstance,
    StrategyConfig,
    TelegramSubscriber,
    SignalLog
)


class ConfigRepository:
    """
    Репозиторий для управления конфигурациями стратегий и ботов.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_bot(self, name: str, token: str) -> BotInstance:
        """
        Создает нового Телеграм-бота.

        Args:
            name (str): Уникальное внутреннее имя бота.
            token (str): API токен от @BotFather.

        Returns:
            BotInstance: Созданный объект бота.
        """
        bot = BotInstance(name=name, token=token)
        self.session.add(bot)
        await self.session.commit()
        await self.session.refresh(bot)
        return bot

    async def add_strategy_config(self, config_data: dict) -> StrategyConfig:
        """
        Добавляет конфигурацию стратегии.

        Args:
            config_data (dict): Словарь с параметрами (instrument, exchange, etc.).

        Returns:
            StrategyConfig: Созданная конфигурация.
        """
        config = StrategyConfig(**config_data)
        self.session.add(config)
        await self.session.commit()
        await self.session.refresh(config)
        return config

    async def get_active_strategies(self) -> List[StrategyConfig]:
        """
        Возвращает список всех активных стратегий, готовых к запуску.

        Использует `selectinload` для жадной загрузки связанного объекта `bot`,
        чтобы избежать ошибок при доступе к атрибутам бота вне сессии.

        Returns:
            List[StrategyConfig]: Список активных конфигураций.
        """
        query = (
            select(StrategyConfig)
            .options(selectinload(StrategyConfig.bot))
            .where(StrategyConfig.is_active == True)
        )
        result = await self.session.execute(query)
        return result.scalars().all()


class SignalRepository:
    """
    Репозиторий для логирования торговых сигналов.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_signal(self, event):
        """
        Сохраняет событие сигнала в базу данных (append-only).

        Args:
            event (SignalEvent): Событие сигнала.
        """
        # TODO: Добавить поле exchange в SignalEvent для корректного логирования
        signal = SignalLog(
            timestamp=event.timestamp,
            exchange="unknown",
            instrument=event.instrument,
            strategy_name=event.strategy_id,
            direction=event.direction,
            price=event.price if event.price else 0.0
        )
        self.session.add(signal)
        await self.session.commit()


class BotRepository:
    """
    Репозиторий для управления подписчиками и состоянием ботов.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active_bots(self) -> List[BotInstance]:
        """
        Возвращает список всех включенных ботов.

        Returns:
            List[BotInstance]: Активные боты.
        """
        query = select(BotInstance).where(BotInstance.is_active == True)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_bot_by_token(self, token: str) -> Optional[BotInstance]:
        """
        Ищет бота по токену.

        Args:
            token (str): API токен.

        Returns:
            Optional[BotInstance]: Найденный бот или None.
        """
        query = select(BotInstance).where(BotInstance.token == token)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def register_subscriber(self, bot_id: int, chat_id: int, username: str = None) -> bool:
        """
        Регистрирует нового подписчика или активирует старого.

        Args:
            bot_id (int): ID бота.
            chat_id (int): Telegram Chat ID пользователя.
            username (str, optional): Имя пользователя.

        Returns:
            bool: True, если подписка успешна (новая или восстановленная).
                  False, если пользователь уже был активен.
        """
        query = select(TelegramSubscriber).where(
            TelegramSubscriber.bot_id == bot_id,
            TelegramSubscriber.chat_id == chat_id
        )
        result = await self.session.execute(query)
        sub = result.scalar_one_or_none()

        if not sub:
            sub = TelegramSubscriber(bot_id=bot_id, chat_id=chat_id, username=username)
            self.session.add(sub)
            await self.session.commit()
            return True  # Новый подписчик

        if not sub.is_active:
            sub.is_active = True
            await self.session.commit()
            return True  # Вернулся старый

        return False  # Уже был активен

    async def get_subscribers_for_strategy(self, strategy_config_id: int) -> List[int]:
        """
        Возвращает список chat_id всех активных подписчиков, которые должны
        получать сигналы от данной стратегии (через привязанного бота).

        Args:
            strategy_config_id (int): ID конфигурации стратегии.

        Returns:
            List[int]: Список ID чатов.
        """
        # 1. Находим config, чтобы узнать ID бота
        config = await self.session.get(StrategyConfig, strategy_config_id)
        if not config or not config.bot_id:
            return []

        # 2. Берем всех активных подписчиков этого бота
        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == config.bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_subscribers_for_bot(self, bot_id: int) -> List[int]:
        """
        Возвращает список chat_id всех активных подписчиков конкретного бота.

        Args:
            bot_id (int): ID бота.

        Returns:
            List[int]: Список ID чатов.
        """
        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()


class PortfolioRepository(IPortfolioRepository):
    """
    Реализация интерфейса сохранения состояния портфеля в БД.
    Обеспечивает персистентность данных между перезапусками.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_portfolio_state(self, config_id: int, state: PortfolioState) -> None:
        """
        Сохраняет текущее состояние портфеля (баланс + позиции).

        Алгоритм:
        1. Ищет или создает запись PortfolioDB.
        2. Обновляет баланс.
        3. Удаляет старые записи позиций и вставляет новые (полная перезапись списка).

        Args:
            config_id (int): ID стратегии.
            state (PortfolioState): Объект состояния из Core-слоя.
        """
        # 1. Ищем существующую запись портфеля
        query = select(PortfolioDB).where(PortfolioDB.strategy_config_id == config_id)
        result = await self.session.execute(query)
        portfolio_db = result.scalar_one_or_none()

        # 2. Если нет - создаем
        if not portfolio_db:
            portfolio_db = PortfolioDB(
                strategy_config_id=config_id,
                initial_capital=state.initial_capital
            )
            self.session.add(portfolio_db)

        # 3. Обновляем поля
        portfolio_db.current_capital = state.current_capital

        # 4. Сохраняем позиции (стратегия: удалить старые -> записать текущие)
        # Если у портфеля уже есть ID (он был в базе), чистим старые позиции
        if portfolio_db.id:
            await self.session.execute(
                delete(PositionDB).where(PositionDB.portfolio_id == portfolio_db.id)
            )

        # Добавляем актуальные позиции из State
        for ticker, pos in state.positions.items():
            pos_db = PositionDB(
                portfolio=portfolio_db,  # Привязка через объект
                instrument=pos.instrument,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                direction=pos.direction.value,  # Enum -> Str
                stop_loss=pos.stop_loss,
                take_profit=pos.take_profit,
                entry_timestamp=pos.entry_timestamp,
                entry_commission=pos.entry_commission
            )
            self.session.add(pos_db)

        await self.session.commit()

    async def load_portfolio_state(self, config_id: int, initial_capital: float) -> PortfolioState:
        """
        Загружает состояние портфеля из БД.

        Если состояние не найдено, возвращает новый пустой объект
        с заданным начальным капиталом.

        Args:
            config_id (int): ID стратегии.
            initial_capital (float): Стартовый капитал (для инициализации, если пусто).

        Returns:
            PortfolioState: Восстановленное или новое состояние.
        """
        # Жадная загрузка позиций для предотвращения N+1 запросов
        query = select(PortfolioDB).options(selectinload(PortfolioDB.positions)) \
            .where(PortfolioDB.strategy_config_id == config_id)

        result = await self.session.execute(query)
        portfolio_db = result.scalar_one_or_none()

        # Если в базе пусто, возвращаем чистый стейт
        if not portfolio_db:
            return PortfolioState(initial_capital=initial_capital)

        # Восстанавливаем стейт
        state = PortfolioState(initial_capital=portfolio_db.initial_capital)
        state.current_capital = portfolio_db.current_capital

        # Восстанавливаем позиции
        for pos_db in portfolio_db.positions:
            # Конвертируем строку обратно в Enum
            direction = TradeDirection.BUY if pos_db.direction == "BUY" else TradeDirection.SELL

            position = Position(
                instrument=pos_db.instrument,
                quantity=pos_db.quantity,
                entry_price=pos_db.entry_price,
                entry_timestamp=pos_db.entry_timestamp,
                direction=direction,
                stop_loss=pos_db.stop_loss,
                take_profit=pos_db.take_profit,
                entry_commission=pos_db.entry_commission
            )
            state.positions[pos_db.instrument] = position

        return state