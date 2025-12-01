"""
Управление сессиями базы данных (Database Session Management).

Этот модуль отвечает за низкоуровневое подключение к базе данных.
Он инициализирует асинхронный движок (Engine), настраивает фабрику сессий
и определяет базовый класс для всех ORM-моделей.

Используемый драйвер: `aiosqlite` (асинхронный SQLite).
"""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.shared.config import config

# Определение пути к файлу БД и автоматическое создание директории, если её нет.
# Это предотвращает ошибки при первом запуске на чистой системе.
DB_PATH = config.DB_PATH

if not DB_PATH.parent.exists():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Строка подключения для SQLAlchemy + aiosqlite
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# Создаем асинхронный движок.
# echo=False отключает вывод всех SQL-запросов в консоль (можно включить для отладки).
engine = create_async_engine(DATABASE_URL, echo=False)

# Фабрика сессий.
# expire_on_commit=False обязателен для асинхронной работы, чтобы объекты
# не "протухали" после закрытия транзакции (так как lazy loading в async ограничен).
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    """
    Базовый класс для всех ORM-моделей.
    Наследуясь от него, классы автоматически регистрируются в метаданных SQLAlchemy.
    """
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Генератор сессий (Dependency Injection).

    Предназначен для использования в конструкциях `Depends()` (например, в FastAPI)
    или как контекстный менеджер. Гарантирует закрытие сессии после использования.

    Yields:
        AsyncSession: Открытая асинхронная сессия БД.
    """
    async with async_session_factory() as session:
        yield session


async def init_models():
    """
    Инициализация схемы базы данных.

    Создает все таблицы, определенные в моделях, наследуемых от `Base`.
    Использует `run_sync`, так как создание таблиц в SQLAlchemy — синхронная операция
    с точки зрения генерации DDL.
    """
    async with engine.begin() as conn:
        # create_all создает таблицы только если их еще нет
        await conn.run_sync(Base.metadata.create_all)