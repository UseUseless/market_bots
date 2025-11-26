from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.shared.config import config

# Путь к файлу БД в корне проекта
DB_PATH = config.DB_PATH

if not DB_PATH.parent.exists():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# Создаем асинхронный движок
engine = create_async_engine(DATABASE_URL, echo=False)

# Фабрика сессий
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Базовый класс для всех моделей
class Base(DeclarativeBase):
    pass

async def get_db():
    """Генератор сессий для использования в контекстных менеджерах."""
    async with async_session_factory() as session:
        yield session

async def init_models():
    """Создает таблицы в БД, если их нет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)