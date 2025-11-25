from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Float, BigInteger
from sqlalchemy.orm import relationship
from datetime import datetime
from app.adapters.database.database import Base


class BotInstance(Base):
    """
    Физический Телеграм-бот (Токен + Имя).
    """
    __tablename__ = "bot_instances"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    token = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    strategies = relationship("StrategyConfig", back_populates="bot", cascade="all, delete-orphan")
    subscribers = relationship("TelegramSubscriber", back_populates="bot", cascade="all, delete-orphan")


class TelegramSubscriber(Base):
    """
    Пользователь, подписанный на конкретного бота.
    """
    __tablename__ = "telegram_subscribers"

    id = Column(Integer, primary_key=True, index=True)
    bot_id = Column(Integer, ForeignKey("bot_instances.id"), nullable=False)

    # BigInteger, так как chat_id в телеграме могут быть большими
    chat_id = Column(BigInteger, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)

    is_active = Column(Boolean, default=True)  # Если заблокировал бота - ставим False
    created_at = Column(DateTime, default=datetime.utcnow)

    bot = relationship("BotInstance", back_populates="subscribers")


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, index=True)
    bot_id = Column(Integer, ForeignKey("bot_instances.id"), nullable=True)

    exchange = Column(String, nullable=False)
    instrument = Column(String, nullable=False)
    interval = Column(String, nullable=False)
    strategy_name = Column(String, nullable=False)
    parameters = Column(JSON, nullable=False, default={})
    risk_manager_type = Column(String, default="FIXED")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    bot = relationship("BotInstance", back_populates="strategies")


class SignalLog(Base):
    __tablename__ = "signal_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    exchange = Column(String, nullable=False)
    instrument = Column(String, nullable=False)
    strategy_name = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    price = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)