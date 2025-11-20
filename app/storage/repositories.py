from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.storage.models import BotInstance, StrategyConfig, SignalLog, TelegramSubscriber


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
        """Возвращает все активные стратегии для запуска."""
        query = select(StrategyConfig).where(StrategyConfig.is_active == True)
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