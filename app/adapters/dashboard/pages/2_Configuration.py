"""
Страница управления конфигурацией (Configuration Page).

Этот модуль предоставляет графический интерфейс (GUI) для администрирования системы.
Позволяет выполнять CRUD-операции (Create, Read, Update, Delete) над сущностями
Ботов и Торговых Стратегий, используя SQLAlchemy ORM.

Технические особенности:
    - Использует синхронный движок SQLAlchemy с драйвером `psycopg2` для совместимости со Streamlit.
    - Реализует транзакционность операций (commit/rollback).
"""

import logging
from typing import Dict, Any

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, select, update, delete
from sqlalchemy.orm import sessionmaker

from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk import RISK_MANAGEMENT_TYPES
from app.shared.config import config
from app.shared.primitives import ExchangeType
from app.infrastructure.database.models import BotInstance, StrategyConfig

logger = logging.getLogger(__name__)

# Глобальные константы
EXCHANGE_INTERVAL_MAPS = config.EXCHANGE_INTERVAL_MAPS

# Настройка страницы
st.set_page_config(
    page_title="Configuration",
    page_icon="⚙️",
    layout="wide"
)
st.title("⚙️ Управление Конфигурацией")

# --- Инициализация БД ---
# Streamlit работает синхронно, поэтому меняем драйвер asyncpg на psycopg2
SYNC_DB_URL = config.DATABASE_URL.replace("+asyncpg", "+psycopg2")
engine = create_engine(SYNC_DB_URL)
SessionLocal = sessionmaker(bind=engine)


def get_all_bots() -> pd.DataFrame:
    """
    Получает список всех ботов через ORM.

    Returns:
        pd.DataFrame: Таблица с колонками [id, name, is_active].
    """
    with SessionLocal() as session:
        stmt = select(BotInstance.id, BotInstance.name, BotInstance.is_active).order_by(BotInstance.id)
        return pd.read_sql(stmt, session.bind)


def render_bots_management_section():
    """
    Отрисовывает интерфейс управления Телеграм-ботами.

    Включает:
    1. Форму добавления нового бота (`INSERT`).
    2. Список существующих ботов с возможностью изменения статуса (`UPDATE`)
       и удаления (`DELETE`).
    """
    st.header("🤖 Телеграм Боты")

    # 1. Форма добавления нового бота
    with st.expander("Добавить нового бота"):
        with st.form("add_bot_form"):
            new_bot_name = st.text_input("Имя бота (внутреннее, например 'MainBot')")
            new_bot_token = st.text_input("Токен (от @BotFather)", type="password")

            submitted_bot = st.form_submit_button("Создать бота")

            if submitted_bot:
                if new_bot_name and new_bot_token:
                    try:
                        with SessionLocal() as session:
                            new_bot = BotInstance(
                                name=new_bot_name,
                                token=new_bot_token,
                                is_active=True
                            )
                            session.add(new_bot)
                            session.commit()
                        st.success(f"Бот {new_bot_name} успешно добавлен!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка сохранения в БД: {e}")
                else:
                    st.warning("Пожалуйста, заполните все поля.")

    # 2. Таблица существующих ботов
    bots_df = get_all_bots()

    if not bots_df.empty:
        st.subheader("Список ботов")
        for _, row in bots_df.iterrows():
            col1, col2, col3 = st.columns([3, 1, 1])

            # Отображение статуса
            status_icon = "🟢" if row['is_active'] else "🔴"
            col1.write(f"### {status_icon} {row['name']} (ID: {row['id']})")

            # Переключатель активности (UPDATE)
            is_active = col2.toggle(
                "Active",
                value=bool(row['is_active']),
                key=f"bot_toggle_{row['id']}"
            )

            if is_active != bool(row['is_active']):
                with SessionLocal() as session:
                    stmt = (
                        update(BotInstance)
                        .where(BotInstance.id == row['id'])
                        .values(is_active=is_active)
                    )
                    session.execute(stmt)
                    session.commit()
                st.toast(f"Статус бота {row['name']} обновлен.")
                st.rerun()

            # Кнопка удаления (DELETE)
            if col3.button("Удалить 🗑️", key=f"del_bot_{row['id']}"):
                try:
                    with SessionLocal() as session:
                        stmt = delete(BotInstance).where(BotInstance.id == row['id'])
                        session.execute(stmt)
                        session.commit()
                    st.success("Бот удален.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Не удалось удалить бота: {e}")
    else:
        st.info("В системе нет добавленных ботов.")


def render_strategies_management_section():
    """
    Отрисовывает интерфейс управления торговыми стратегиями.

    Включает:
    1. Форму создания новой конфигурации стратегии (связывание бота, биржи и алгоритма).
    2. Список активных конфигураций с управлением через ORM.
    """
    st.divider()
    st.header("📈 Торговые Стратегии")

    # Получаем список доступных ботов для привязки
    bots_df = get_all_bots()

    # 1. Форма добавления стратегии
    with st.expander("Добавить новую стратегию", expanded=True):
        if bots_df.empty:
            st.warning("⚠️ Сначала добавьте хотя бы одного бота, чтобы создать стратегию.")
        else:
            with st.form("add_strat_form"):
                # Блок выбора связей (Бот + Алгоритм)
                c1, c2 = st.columns(2)
                selected_bot_name = c1.selectbox("Бот для уведомлений", bots_df['name'].tolist())
                selected_strategy_cls = c2.selectbox("Алгоритм стратегии", list(AVAILABLE_STRATEGIES.keys()))

                # Блок настроек рынка и риска
                c3, c4, c5, c6 = st.columns(4)
                exchange = c3.selectbox("Биржа", [ExchangeType.BYBIT, ExchangeType.TINKOFF])
                instrument = c4.text_input("Тикер инструмента", value="BTCUSDT").upper().strip()

                # Динамические интервалы в зависимости от выбранной биржи
                intervals = list(EXCHANGE_INTERVAL_MAPS.get(exchange, {}).keys())
                # Fallback, если биржа не выбрана или конфиг пуст
                interval_options = intervals if intervals else ["1min", "5min", "15min", "1hour"]
                interval = c5.selectbox("Таймфрейм", interval_options)

                risk_manager_type = c6.selectbox("Риск-менеджер", list(RISK_MANAGEMENT_TYPES.keys()))

                submitted_strat = st.form_submit_button("Добавить стратегию")

                if submitted_strat:
                    # Поиск ID бота по выбранному имени
                    bot_id = bots_df[bots_df['name'] == selected_bot_name].iloc[0]['id']

                    try:
                        with SessionLocal() as session:
                            new_config = StrategyConfig(
                                bot_id=int(bot_id),
                                exchange=exchange,
                                instrument=instrument,
                                interval=interval,
                                strategy_name=selected_strategy_cls,
                                parameters={},  # Пустой dict, стратегия возьмет default params
                                is_active=True,
                                risk_manager_type=risk_manager_type
                            )
                            session.add(new_config)
                            session.commit()
                        st.success("Стратегия успешно добавлена!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка сохранения стратегии: {e}")

    # 2. Таблица активных конфигураций
    # Загружаем данные с Join, чтобы получить имя бота
    with SessionLocal() as session:
        stmt = (
            select(
                StrategyConfig.id,
                StrategyConfig.exchange,
                StrategyConfig.instrument,
                StrategyConfig.interval,
                StrategyConfig.strategy_name,
                StrategyConfig.is_active,
                StrategyConfig.risk_manager_type,
                BotInstance.name.label("bot_name")
            )
            .join(BotInstance, StrategyConfig.bot_id == BotInstance.id, isouter=True)
            .order_by(StrategyConfig.id)
        )
        strats_df = pd.read_sql(stmt, session.bind)

    if not strats_df.empty:
        st.subheader("Активные конфигурации")

        # Интерактивная таблица (только для чтения)
        st.dataframe(
            strats_df,
            column_config={
                "is_active": st.column_config.CheckboxColumn("Active", disabled=True)
            },
            use_container_width=True,
            hide_index=True
        )

        st.subheader("Управление")
        for _, row in strats_df.iterrows():
            with st.container(border=True):
                c1, c2, c3 = st.columns([4, 1, 1])

                # Описание конфигурации
                label = (f"**{row['exchange'].upper()} {row['instrument']}** ({row['interval']}) "
                         f"— {row['strategy_name']} [{row['risk_manager_type']}] ➡️ {row['bot_name']}")
                c1.markdown(label)

                # Тоггл активности (UPDATE)
                is_active = c2.toggle(
                    "On/Off",
                    value=bool(row['is_active']),
                    key=f"strat_toggle_{row['id']}"
                )

                if is_active != bool(row['is_active']):
                    with SessionLocal() as session:
                        stmt = (
                            update(StrategyConfig)
                            .where(StrategyConfig.id == row['id'])
                            .values(is_active=is_active)
                        )
                        session.execute(stmt)
                        session.commit()
                    st.rerun()

                # Кнопка удаления (DELETE)
                if c3.button("🗑️", key=f"del_strat_{row['id']}"):
                    try:
                        with SessionLocal() as session:
                            stmt = delete(StrategyConfig).where(StrategyConfig.id == row['id'])
                            session.execute(stmt)
                            session.commit()
                        st.success("Стратегия удалена.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка удаления: {e}")
    else:
        st.info("Нет настроенных стратегий.")


if __name__ == "__main__":
    render_bots_management_section()
    render_strategies_management_section()