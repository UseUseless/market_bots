"""
Модели базы данных (SQLAlchemy ORM).

Этот модуль определяет схему базы данных приложения. Каждый класс соответствует
таблице в БД. Модели описывают сущности (Боты, Стратегии, Пользователи),
их атрибуты и связи между ними.

Используется декларативный стиль SQLAlchemy.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Float, BigInteger
from sqlalchemy.orm import relationship
from app.infrastructure.database.session import Base


class BotInstance(Base):
    """
    Модель физического Телеграм-бота.

    Хранит учетные данные для подключения к API Telegram. Один бот может
    обслуживать множество стратегий и иметь множество подписчиков.

    Attributes:
        id (int): Уникальный идентификатор (PK).
        name (str): Внутреннее имя бота для удобства (уникальное).
        token (str): API токен, полученный от @BotFather.
        is_active (bool): Флаг глобального включения/выключения бота.
        created_at (datetime): Дата добавления в систему.
    """
    __tablename__ = "bot_instances"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    token = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    # При удалении бота удаляются все связанные конфиги стратегий и подписчики
    strategies = relationship("StrategyConfig", back_populates="bot", cascade="all, delete-orphan")
    subscribers = relationship("TelegramSubscriber", back_populates="bot", cascade="all, delete-orphan")


class TelegramSubscriber(Base):
    """
    Модель подписчика (пользователя Telegram).

    Связывает конкретного пользователя Telegram с конкретным ботом в нашей системе.

    Attributes:
        id (int): Уникальный идентификатор записи (PK).
        bot_id (int): Ссылка на бота, на которого подписан пользователь (FK).
        chat_id (int): Уникальный ID чата в Telegram (может быть отрицательным для групп).
        username (str): Имя пользователя (опционально).
        first_name (str): Имя (опционально).
        is_active (bool): Статус подписки (True = получает сигналы).
    """
    __tablename__ = "telegram_subscribers"

    id = Column(Integer, primary_key=True, index=True)
    bot_id = Column(Integer, ForeignKey("bot_instances.id"), nullable=False)

    # BigInteger обязателен, так как Telegram ID превышают диапазон обычного Integer
    chat_id = Column(BigInteger, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    bot = relationship("BotInstance", back_populates="subscribers")


class StrategyConfig(Base):
    """
    Конфигурация экземпляра торговой стратегии.

    Описывает, какую стратегию, на какой бирже и с какими параметрами нужно запускать.
    Привязана к конкретному боту, который будет транслировать сигналы этой стратегии.

    Attributes:
        id (int): Уникальный идентификатор конфига (PK).
        bot_id (int): Ссылка на бота для уведомлений (FK).
        exchange (str): Название биржи (tinkoff/bybit).
        instrument (str): Тикер инструмента (например, BTCUSDT).
        interval (str): Таймфрейм (например, 1min).
        strategy_name (str): Имя класса стратегии из `app.strategies`.
        parameters (dict): JSON с настройками стратегии (периоды индикаторов и т.д.).
        risk_manager_type (str): Тип риск-менеджмента (FIXED, ATR).
        is_active (bool): Флаг запуска стратегии в Live-режиме.
    """
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

    # Связь 1-к-1 с состоянием портфеля. При удалении конфига удаляется и портфель.
    portfolio_state = relationship("PortfolioDB", back_populates="strategy_config", uselist=False,
                                   cascade="all, delete-orphan")


class SignalLog(Base):
    """
    Журнал истории сигналов.

    Используется для аналитики, отображения в Dashboard и отладки.
    Это append-only таблица.

    Attributes:
        timestamp (datetime): Время генерации сигнала.
        exchange (str): Биржа.
        instrument (str): Инструмент.
        strategy_name (str): Имя стратегии.
        direction (str): Направление (BUY/SELL).
        price (float): Цена, по которой был сгенерирован сигнал.
    """
    __tablename__ = "signal_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False)
    exchange = Column(String, nullable=False)
    instrument = Column(String, nullable=False)
    strategy_name = Column(String, nullable=False)
    direction = Column(String, nullable=False)
    price = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PortfolioDB(Base):
    """
    Персистентное состояние портфеля для стратегии.

    Позволяет сохранять финансовый результат и открытые позиции между
    перезапусками приложения. Связана 1-к-1 с `StrategyConfig`.

    Attributes:
        strategy_config_id (int): Ссылка на конфиг стратегии (FK, Unique).
        current_capital (float): Текущий баланс стратегии.
        initial_capital (float): Стартовый капитал.
    """
    __tablename__ = "portfolios"

    id = Column(Integer, primary_key=True, index=True)
    strategy_config_id = Column(Integer, ForeignKey("strategy_configs.id"), unique=True, nullable=False)

    current_capital = Column(Float, default=0.0)
    initial_capital = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    strategy_config = relationship("StrategyConfig", back_populates="portfolio_state")
    positions = relationship("PositionDB", back_populates="portfolio", cascade="all, delete-orphan")


class PositionDB(Base):
    """
    Активная позиция в базе данных.

    Хранит информацию о текущих открытых сделках для восстановления
    состояния RiskManager после перезагрузки.

    Attributes:
        portfolio_id (int): Ссылка на портфель (FK).
        instrument (str): Тикер.
        quantity (float): Объем позиции.
        entry_price (float): Цена входа.
        direction (str): Направление (BUY/SELL).
        stop_loss (float): Уровень стоп-лосса.
        take_profit (float): Уровень тейк-профита.
    """
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)

    instrument = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    direction = Column(String, nullable=False)

    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)

    entry_timestamp = Column(DateTime, nullable=False)
    entry_commission = Column(Float, default=0.0)

    portfolio = relationship("PortfolioDB", back_populates="positions")