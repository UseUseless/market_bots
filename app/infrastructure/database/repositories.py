"""
Репозитории для доступа к данным (Data Access Layer).
"""

from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.database.models import (
    BotInstance, StrategyConfig, TelegramSubscriber, SignalLog
)
from app.shared.events import SignalEvent

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

    async def log_signal(self, event: SignalEvent):
        """
        Сохраняет событие сигнала в БД.
        """
        signal = SignalLog(
            timestamp=event.timestamp,
            exchange="unknown", # В событии этого нет, можно прокинуть через конфиг или оставить так
            instrument=event.instrument,
            strategy_name=event.strategy_name, # Теперь поле есть
            direction=event.direction,
            price=event.price
        )
        self.session.add(signal)
        await self.session.commit()


class BotRepository:
    # ... (Код BotRepository оставляем без изменений, он корректен) ...
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all_active_bots(self) -> List[BotInstance]:
        query = select(BotInstance).where(BotInstance.is_active == True)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_bot_by_token(self, token: str) -> Optional[BotInstance]:
        query = select(BotInstance).where(BotInstance.token == token)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def register_subscriber(self, bot_id: int, chat_id: int, username: str = None) -> bool:
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
            return True

        if not sub.is_active:
            sub.is_active = True
            await self.session.commit()
            return True

        return False

    async def get_subscribers_for_strategy(self, strategy_config_id: int) -> List[int]:
        # Внимание: здесь логика была привязана к config_id.
        # В SignalEvent мы передаем strategy_name.
        # TelegramSignalSender должен будет найти config по имени стратегии.
        # Оставим этот метод, он пригодится.
        config = await self.session.get(StrategyConfig, strategy_config_id)
        if not config or not config.bot_id:
            return []

        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == config.bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_all_subscribers_for_bot(self, bot_id: int) -> List[int]:
        query = select(TelegramSubscriber.chat_id).where(
            TelegramSubscriber.bot_id == bot_id,
            TelegramSubscriber.is_active == True
        )
        result = await self.session.execute(query)
        return result.scalars().all()