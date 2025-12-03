"""
Страница управления конфигурацией (Configuration Page).

Этот модуль предоставляет графический интерфейс (GUI) для администрирования системы.
Позволяет выполнять CRUD-операции (Create, Read, Update, Delete) над сущностями
Ботов и Торговых Стратегий.

Технические особенности:
- Использует синхронный движок SQLAlchemy с драйвером `psycopg2`.
- Использует параметризованные SQL-запросы для защиты от инъекций.
"""

import logging
from typing import Dict, Any

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.shared.config import config
from app.shared.primitives import ExchangeType

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
SYNC_DB_URL = config.DATABASE_URL.replace("+asyncpg", "+psycopg2")
engine = create_engine(SYNC_DB_URL)


def _execute_transaction(query_str: str, params: Dict[str, Any] = {}) -> None:
    """
    Выполняет транзакционный SQL-запрос на изменение данных (INSERT, UPDATE, DELETE).

    Использует `engine.begin()`, который автоматически открывает транзакцию
    и фиксирует её (commit) при успешном завершении блока или откатывает (rollback) при ошибке.

    Args:
        query_str (str): Текст SQL запроса с именованными параметрами (например, :id).
        params (Dict[str, Any]): Словарь значений для подстановки в запрос. Defaults to {}.
    """
    with engine.begin() as conn:
        conn.execute(text(query_str), params)


def _fetch_data_frame(query_str: str, params: Dict[str, Any] = {}) -> pd.DataFrame:
    """
    Выполняет SQL-запрос на чтение данных и возвращает результат в виде Pandas DataFrame.

    Args:
        query_str (str): Текст SQL запроса SELECT.
        params (Dict[str, Any]): Словарь параметров запроса. Defaults to {}.

    Returns:
        pd.DataFrame: Таблица с результатами выборки.
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query_str), conn, params=params)


def render_bots_management_section():
    """
    Отрисовывает интерфейс управления Телеграм-ботами.

    Включает:
    1. Форму добавления нового бота (имя, токен).
    2. Список существующих ботов с возможностью включения/выключения и удаления.
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
                        _execute_transaction(
                            "INSERT INTO bot_instances (name, token, is_active) VALUES (:name, :token, true)",
                            {"name": new_bot_name, "token": new_bot_token}
                        )
                        st.success(f"Бот {new_bot_name} успешно добавлен!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка сохранения в БД: {e}")
                else:
                    st.warning("Пожалуйста, заполните все поля.")

    # 2. Таблица существующих ботов
    bots_df = _fetch_data_frame("SELECT id, name, is_active FROM bot_instances ORDER BY id")

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
                _execute_transaction(
                    "UPDATE bot_instances SET is_active = :active WHERE id = :id",
                    {"active": is_active, "id": row['id']}
                )
                st.toast(f"Статус бота {row['name']} обновлен.")
                st.rerun()

            # Кнопка удаления (DELETE)
            if col3.button("Удалить 🗑️", key=f"del_bot_{row['id']}"):
                try:
                    _execute_transaction("DELETE FROM bot_instances WHERE id = :id", {"id": row['id']})
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
    1. Форму создания новой конфигурации стратегии (выбор биржи, инструмента, таймфрейма, РМ).
    2. Список активных конфигураций с возможностью деактивации и удаления.
    """
    st.divider()
    st.header("📈 Торговые Стратегии")

    # Получаем список доступных ботов для привязки
    bots_df = _fetch_data_frame("SELECT id, name FROM bot_instances")

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
                interval = c5.selectbox("Таймфрейм", intervals if intervals else ["1min"])

                risk_manager_type = c6.selectbox("Риск-менеджер", list(AVAILABLE_RISK_MANAGERS.keys()))

                submitted_strat = st.form_submit_button("Добавить стратегию")

                if submitted_strat:
                    # Поиск ID бота по выбранному имени
                    bot_id = bots_df[bots_df['name'] == selected_bot_name].iloc[0]['id']

                    try:
                        # В parameters всегда пишем пустой JSON "{}",
                        # чтобы стратегия брала настройки по умолчанию из своего Python-класса.
                        _execute_transaction("""
                            INSERT INTO strategy_configs 
                            (bot_id, exchange, instrument, interval, strategy_name, 
                             parameters, is_active, risk_manager_type)
                            VALUES (:bot_id, :ex, :instr, :inter, :strat, '{}', true, :rm)
                        """, {
                            "bot_id": int(bot_id),
                            "ex": exchange,
                            "instr": instrument,
                            "inter": interval,
                            "strat": selected_strategy_cls,
                            "rm": risk_manager_type
                        })
                        st.success("Стратегия успешно добавлена!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка сохранения стратегии: {e}")

    # 2. Таблица активных конфигураций
    strats_df = _fetch_data_frame("""
        SELECT s.id, s.exchange, s.instrument, s.interval, s.strategy_name, 
               s.is_active, s.risk_manager_type, b.name as bot_name
        FROM strategy_configs s
        LEFT JOIN bot_instances b ON s.bot_id = b.id
        ORDER BY s.id
    """)

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
                    _execute_transaction(
                        "UPDATE strategy_configs SET is_active = :act WHERE id = :id",
                        {"act": is_active, "id": row['id']}
                    )
                    st.rerun()

                # Кнопка удаления (DELETE)
                if c3.button("🗑️", key=f"del_strat_{row['id']}"):
                    try:
                        _execute_transaction("DELETE FROM strategy_configs WHERE id = :id", {"id": row['id']})
                        st.success("Стратегия удалена.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка удаления: {e}")
    else:
        st.info("Нет настроенных стратегий.")


if __name__ == "__main__":
    render_bots_management_section()
    render_strategies_management_section()