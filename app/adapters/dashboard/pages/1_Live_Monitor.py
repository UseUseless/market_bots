"""
Страница мониторинга сигналов (Live Monitor).

Этот модуль отвечает за визуализацию текущего состояния торговой системы в реальном времени.
Он подключается к базе данных PostgreSQL, извлекает оперативные метрики через ORM
и отображает их в интерфейсе Streamlit.

Функциональность:
    1. **KPI Метрики**: Количество активных ботов, стратегий, подписчиков и время последнего сигнала.
    2. **Лента сигналов**: Таблица последних сгенерированных сигналов с цветовой кодировкой.
    3. **Статус системы**: Сводные таблицы по активным ботам и запущенным стратегиям.
"""

import logging
from typing import Tuple

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, select, func, desc
from sqlalchemy.orm import sessionmaker

from app.shared.config import config
from app.adapters.dashboard.db import get_session_factory
from app.infrastructure.database.models import (
    BotInstance,
    StrategyConfig,
    SignalLog,
    TelegramSubscriber
)

logger = logging.getLogger(__name__)

# Настройка страницы Streamlit
st.set_page_config(
    page_title="Live Monitor",
    page_icon="🚀",
    layout="wide"
)
st.title("🚀 Live Signal Monitor")

# Инициализация БД
SessionLocal = get_session_factory()

def load_operational_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Загружает полный набор оперативных данных из базы данных используя ORM.

    Выполняет четыре запроса в рамках одной сессии для оптимизации производительности.

    Returns:
        Tuple[pd.DataFrame, ...]: Кортеж из четырех DataFrame:
            - bots: Список всех ботов и их статусов.
            - strategies: Активные стратегии с привязкой к именам ботов.
            - signals: Последние 20 торговых сигналов.
            - subscribers: Агрегированная статистика подписчиков по ботам.
    """
    try:
        with SessionLocal() as session:
            # 1. Активные Боты
            # Эквивалент: SELECT id, name, is_active, created_at FROM bot_instances
            stmt_bots = select(
                BotInstance.id,
                BotInstance.name,
                BotInstance.is_active,
                BotInstance.created_at
            )
            bots = pd.read_sql(stmt_bots, session.bind)

            # 2. Активные Стратегии (с Join для получения имени бота)
            # Эквивалент: SELECT s.*, b.name FROM strategy_configs s LEFT JOIN bot_instances b ...
            stmt_strats = (
                select(
                    StrategyConfig.id,
                    StrategyConfig.strategy_name,
                    StrategyConfig.exchange,
                    StrategyConfig.instrument,
                    StrategyConfig.interval,
                    StrategyConfig.is_active,
                    BotInstance.name.label("bot_name")
                )
                .join(BotInstance, StrategyConfig.bot_id == BotInstance.id, isouter=True)
                .where(StrategyConfig.is_active == True)
            )
            strats = pd.read_sql(stmt_strats, session.bind)

            # 3. Лента Сигналов (последние 20)
            # Эквивалент: SELECT ... FROM signal_logs ORDER BY timestamp DESC LIMIT 20
            stmt_signals = (
                select(
                    SignalLog.timestamp,
                    SignalLog.exchange,
                    SignalLog.instrument,
                    SignalLog.strategy_name,
                    SignalLog.direction,
                    SignalLog.price
                )
                .order_by(desc(SignalLog.timestamp))
                .limit(20)
            )
            signals = pd.read_sql(stmt_signals, session.bind)

            # 4. Статистика подписчиков (Агрегация)
            # Эквивалент: SELECT b.name, COUNT(t.id) FROM subscribers t JOIN bots b ... GROUP BY b.name
            stmt_subs = (
                select(
                    BotInstance.name.label("bot_name"),
                    func.count(TelegramSubscriber.id).label("sub_count")
                )
                .join(BotInstance, TelegramSubscriber.bot_id == BotInstance.id)
                .where(TelegramSubscriber.is_active == True)
                .group_by(BotInstance.name)
            )
            subs = pd.read_sql(stmt_subs, session.bind)

            return bots, strats, signals, subs

    except Exception as e:
        logger.error(f"Dashboard Data Load Error: {e}")
        st.error(f"Ошибка подключения к базе данных: {e}")
        # Возвращаем пустые структуры, чтобы UI не упал
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


def _style_direction_cell(val: str) -> str:
    """
    Применяет CSS-стили к ячейке направления торговли.

    Args:
        val (str): Значение направления ('BUY' или 'SELL').

    Returns:
        str: CSS-строка стилей.
    """
    color = '#d62728' if val == 'SELL' else '#2ca02c'
    return f'color: {color}; font-weight: bold'


def main():
    """
    Основная функция рендеринга страницы (Controller).

    Отвечает за:
    1. Обработку кнопки обновления.
    2. Вызов функции загрузки данных.
    3. Отрисовку метрик (KPI).
    4. Отрисовку таблиц с данными.
    """
    # Кнопка ручного обновления состояния
    if st.button('🔄 Обновить данные'):
        st.rerun()

    # Загрузка данных
    df_bots, df_strats, df_signals, df_subs = load_operational_data()

    # --- СЕКЦИЯ 1: KPI МЕТРИКИ ---
    col1, col2, col3, col4 = st.columns(4)

    # Подсчет активных ботов
    active_bots_count = len(df_bots[df_bots['is_active'] == True]) if not df_bots.empty else 0
    col1.metric("Активных ботов", active_bots_count)

    col2.metric("Активных стратегий", len(df_strats))

    total_subs = df_subs['sub_count'].sum() if not df_subs.empty else 0
    col3.metric("Всего подписчиков", int(total_subs))

    last_sig_time = "Нет данных"
    if not df_signals.empty:
        # Убираем микросекунды для чистоты отображения
        last_sig_time = str(df_signals.iloc[0]['timestamp']).split('.')[0]

    col4.metric("Последний сигнал", last_sig_time)

    st.markdown("---")

    # --- СЕКЦИЯ 2: ТАБЛИЦЫ ---
    c1, c2 = st.columns([2, 1])

    with c1:
        st.subheader("📡 Лента Сигналов")
        if not df_signals.empty:
            # Применение стилей к DataFrame
            styled_df = df_signals.style.map(_style_direction_cell, subset=['direction'])
            st.dataframe(styled_df, use_container_width=True, height=400)
        else:
            st.info("Лента сигналов пуста. Ожидание событий...")

    with c2:
        st.subheader("🤖 Статус Ботов")
        if not df_subs.empty:
            st.dataframe(df_subs, use_container_width=True, hide_index=True)
        else:
            st.caption("Нет данных о подписчиках.")

        st.subheader("⚙️ Активные Пары")
        if not df_strats.empty:
            st.dataframe(
                df_strats[['bot_name', 'instrument', 'interval', 'strategy_name']],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.warning("Нет активных стратегий! Перейдите на вкладку Configuration.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        st.error(f"Критическая ошибка приложения: {e}")