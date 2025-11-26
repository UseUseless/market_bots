from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import List, Optional

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
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_bot(self, name: str, token: str) -> BotInstance:
        bot = BotInstance(name=name, token=token)
        self.session.add(bot)
        await self.session.commit()
        await self.session.refresh(bot)
        return bot

    async def add_strategy_config(self, config_data: dict) -> StrategyConfig:
        config = StrategyConfig(**config_data)
        self.session.add(config)
        await self.session.commit()
        await self.session.refresh(config)
        return config

    async def get_active_strategies(self) -> List[StrategyConfig]:
        """
        Возвращает все активные стратегии для запуска.
        Использует selectinload для подгрузки связанных данных (бота),
        чтобы избежать DetachedInstanceError после закрытия сессии.
        """
        query = (
            select(StrategyConfig)
            .options(selectinload(StrategyConfig.bot))
            .where(StrategyConfig.is_active == True)
        )
        result = await self.session.execute(query)
        return result.scalars().all()


class SignalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_signal(self, event):
        """Сохраняет сигнал в историю."""
        signal = SignalLog(
            timestamp=event.timestamp,
            exchange="unknown", # В будущем добавим в Event поле exchange
            instrument=event.instrument,
            strategy_name=event.strategy_id,
            direction=event.direction,
            price=0.0 # Добавим цену позже
        )
        self.session.add(signal)
        await self.session.commit()


class BotRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active_bots(self) -> List[BotInstance]:
        """Получает токены всех активных ботов для запуска."""
        query = select(BotInstance).where(BotInstance.is_active == True)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_bot_by_token(self, token: str) -> Optional[BotInstance]:
        query = select(BotInstance).where(BotInstance.token == token)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def register_subscriber(self, bot_id: int, chat_id: int, username: str = None):
        """Регистрирует пользователя, если его еще нет."""
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

        return False  # Уже был

    async def get_subscribers_for_strategy(self, strategy_config_id: int) -> List[int]:
        """
        Магия SQL: Находим бота, к которому привязана стратегия,
        и берем всех его активных подписчиков.
        """
        # 1. Находим config
        config = await self.session.get(StrategyConfig, strategy_config_id)
        if not config or not config.bot_id:
            return []

        # 2. Берем подписчиков этого бота
        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == config.bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_subscribers_for_bot(self, bot_id: int) -> List[int]:
        """Возвращает список chat_id всех активных подписчиков этого бота."""
        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()


class PortfolioRepository(IPortfolioRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_portfolio_state(self, config_id: int, state: PortfolioState) -> None:
        """
        Сохраняет состояние: обновляет PortfolioDB и перезаписывает PositionDB.
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
        # Если портфель уже существовал, у него есть ID, и мы можем удалить старые позиции.
        # Если портфель только что создан (не закомичен), удалять нечего.
        if portfolio_db.id:
            await self.session.execute(
                delete(PositionDB).where(PositionDB.portfolio_id == portfolio_db.id)
            )

        # Добавляем актуальные позиции из State
        for ticker, pos in state.positions.items():
            pos_db = PositionDB(
                portfolio=portfolio_db,  # Привязка объекта (SQLAlchemy сама разберется с ID)
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
        Восстанавливает PortfolioState из БД.
        """
        # Жадная загрузка позиций (joinedload/selectinload)
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