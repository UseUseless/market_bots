"""
Модуль подключения к БД для Streamlit.
Реализует Singleton через кэширование ресурсов.
"""
import logging
import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.shared.config import config

logger = logging.getLogger(__name__)

@st.cache_resource
def get_db_engine():
    """
    Создает и кэширует движок SQLAlchemy.
    Выполняется ОДИН раз на весь жизненный цикл процесса Streamlit.
    """
    # Подменяем драйвер на синхронный (asyncpg -> psycopg2),
    # так как Streamlit работает в синхронном режиме.
    sync_db_url = config.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    
    logger.info("Initializing global DB engine for Dashboard...")
    
    return create_engine(
        sync_db_url,
        # pool_pre_ping=True: перед каждым запросом проверяет, жива ли связь.
        # Если Postgres разорвал соединение (например, ночью),
        # SQLAlchemy переподключится автоматически, а не выкинет ошибку.
        pool_pre_ping=True,
        
        # Настройки пула (опционально, но полезно для прода):
        pool_size=5,        # Держим 5 открытых соединений
        max_overflow=10     # При нагрузке можем открыть еще 10
    )

def get_session_factory():
    """
    Возвращает фабрику сессий, привязанную к кэшированному движку.
    """
    engine = get_db_engine()
    return sessionmaker(bind=engine)