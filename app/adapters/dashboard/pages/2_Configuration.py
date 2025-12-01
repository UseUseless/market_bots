"""
Страница управления конфигурацией (Configuration Page).

Этот модуль предоставляет графический интерфейс (GUI) для администрирования
системы. Позволяет управлять двумя основными сущностями:
1. **Телеграм Боты:** Добавление токенов, включение/отключение отправки.
2. **Торговые Стратегии:** Создание связок "Стратегия-Инструмент-Бот",
   настройка таймфреймов и риск-менеджмента.

Примечание по архитектуре:
    В этом модуле используется прямой синхронный доступ к SQLite (`sqlite3`) вместо
    асинхронных репозиториев. Это сделано намеренно, так как Streamlit является
    синхронным фреймворком, и использование `asyncio` здесь усложнило бы код
    без существенного выигрыша в производительности для задач администрирования.
"""

import sqlite3
import json
import logging
from typing import List, Tuple

import pandas as pd
import streamlit as st

from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.manager import AVAILABLE_RISK_MANAGERS
from app.shared.config import config
from app.shared.primitives import ExchangeType

logger = logging.getLogger(__name__)

# Глобальные константы
EXCHANGE_INTERVAL_MAPS = config.EXCHANGE_INTERVAL_MAPS
DB_PATH = config.DB_PATH

# Настройка страницы
st.set_page_config(
    page_title="Configuration",
    page_icon="⚙️",
    layout="wide"
)
st.title("⚙️ Управление Конфигурацией")


# --- Хелперы для работы с БД (Data Access Helpers) ---

def _execute_query(query: str, params: tuple = ()) -> None:
    """
    Выполняет SQL-запрос на изменение данных (INSERT, UPDATE, DELETE).
    """
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cur.execute(query, params)
        con.commit()


def _fetch_data(query: str) -> pd.DataFrame:
    """
    Выполняет SQL-запрос на чтение и возвращает DataFrame.
    """
    with sqlite3.connect(DB_PATH) as con:
        return pd.read_sql(query, con)


# --- РАЗДЕЛ 1: БОТЫ ---

def render_bots_section():
    """Отрисовывает секцию управления ботами."""
    st.header("🤖 Телеграм Боты")

    # 1. Форма добавления
    with st.expander("Добавить нового бота"):
        with st.form("add_bot_form"):
            new_bot_name = st.text_input("Имя бота (внутреннее, например 'MainBot')")
            new_bot_token = st.text_input("Токен (от @BotFather)", type="password")

            submitted_bot = st.form_submit_button("Создать бота")

            if submitted_bot:
                if new_bot_name and new_bot_token:
                    try:
                        _execute_query(
                            "INSERT INTO bot_instances (name, token, is_active) VALUES (?, ?, 1)",
                            (new_bot_name, new_bot_token)
                        )
                        st.success(f"Бот {new_bot_name} успешно добавлен!")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Ошибка: Имя бота или токен уже существуют.")
                    except Exception as e:
                        st.error(f"Ошибка БД: {e}")
                else:
                    st.warning("Заполните все поля.")

    # 2. Таблица и управление
    bots_df = _fetch_data("SELECT id, name, is_active FROM bot_instances")

    if not bots_df.empty:
        st.subheader("Список ботов")

        # Итерируемся по строкам для создания кнопок управления
        for _, row in bots_df.iterrows():
            col1, col2, col3 = st.columns([3, 1, 1])

            # Имя и ID
            status_emoji = "🟢" if row['is_active'] else "🔴"
            col1.write(f"### {status_emoji} {row['name']} (ID: {row['id']})")

            # Переключатель активности
            is_active = col2.toggle(
                "Active",
                value=bool(row['is_active']),
                key=f"bot_toggle_{row['id']}"
            )

            if is_active != bool(row['is_active']):
                _execute_query(
                    "UPDATE bot_instances SET is_active = ? WHERE id = ?",
                    (is_active, row['id'])
                )
                st.toast(f"Статус бота {row['name']} обновлен!")
                st.rerun()

            # Кнопка удаления
            if col3.button("Удалить 🗑️", key=f"del_bot_{row['id']}"):
                try:
                    _execute_query("DELETE FROM bot_instances WHERE id = ?", (row['id'],))
                    st.success("Бот удален.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Не удалось удалить: {e}")
    else:
        st.info("В системе нет добавленных ботов. Создайте первого бота выше.")


# --- РАЗДЕЛ 2: СТРАТЕГИИ ---

def render_strategies_section():
    """Отрисовывает секцию управления стратегиями."""
    st.divider()
    st.header("📈 Торговые Стратегии")

    # Подгружаем список ботов для привязки
    bots_df = _fetch_data("SELECT id, name FROM bot_instances")

    # 1. Форма добавления
    with st.expander("Добавить новую стратегию", expanded=True):
        if bots_df.empty:
            st.warning("⚠️ Сначала добавьте хотя бы одного бота, чтобы создать стратегию.")
        else:
            with st.form("add_strat_form"):
                # Настройки привязки
                c1, c2 = st.columns(2)
                selected_bot_name = c1.selectbox("Бот для уведомлений", bots_df['name'].tolist())
                selected_strategy_cls = c2.selectbox("Алгоритм стратегии", list(AVAILABLE_STRATEGIES.keys()))

                # Настройки рынка
                c3, c4, c5 = st.columns(3)
                exchange = c3.selectbox("Биржа", [ExchangeType.BYBIT, ExchangeType.TINKOFF])
                instrument = c4.text_input("Тикер инструмента", value="BTCUSDT").upper()

                # Динамические интервалы в зависимости от биржи
                intervals = list(EXCHANGE_INTERVAL_MAPS.get(exchange, {}).keys())
                interval = c5.selectbox("Таймфрейм", intervals if intervals else ["1min"])

                # Настройки риска
                c6, c7 = st.columns(2)
                risk_manager_type = c6.selectbox("Риск-менеджер", list(AVAILABLE_RISK_MANAGERS.keys()))
                # Пока поддерживаем редактирование JSON напрямую, в будущем можно сделать форму
                params_json = c7.text_area("Параметры (JSON)", value="{}", height=100)

                submitted_strat = st.form_submit_button("Добавить стратегию")

                if submitted_strat:
                    # Валидация JSON
                    try:
                        json.loads(params_json)
                    except json.JSONDecodeError:
                        st.error("Ошибка: Некорректный формат JSON параметров.")
                        return

                    # Поиск ID бота
                    bot_id = bots_df[bots_df['name'] == selected_bot_name].iloc[0]['id']

                    try:
                        _execute_query("""
                            INSERT INTO strategy_configs 
                            (bot_id, exchange, instrument, interval, strategy_name, 
                             parameters, is_active, risk_manager_type)
                            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                        """, (
                            int(bot_id), exchange, instrument, interval,
                            selected_strategy_cls, params_json, risk_manager_type
                        ))
                        st.success("Стратегия успешно добавлена!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка сохранения в БД: {e}")

    # 2. Таблица стратегий
    strats_df = _fetch_data("""
        SELECT s.id, s.exchange, s.instrument, s.interval, s.strategy_name, 
               s.is_active, s.risk_manager_type, b.name as bot_name
        FROM strategy_configs s
        LEFT JOIN bot_instances b ON s.bot_id = b.id
    """)

    if not strats_df.empty:
        st.subheader("Активные конфигурации")

        # Красивая таблица
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
                col1, col2, col3 = st.columns([4, 1, 1])

                # Описание
                label = (f"**{row['exchange'].upper()} {row['instrument']}** ({row['interval']}) "
                         f"— {row['strategy_name']} [{row['risk_manager_type']}] ➡️ {row['bot_name']}")
                col1.markdown(label)

                # Тоггл активности
                is_active = col2.toggle(
                    "On/Off",
                    value=bool(row['is_active']),
                    key=f"strat_toggle_{row['id']}"
                )

                if is_active != bool(row['is_active']):
                    _execute_query(
                        "UPDATE strategy_configs SET is_active = ? WHERE id = ?",
                        (is_active, row['id'])
                    )
                    st.rerun()

                # Удаление
                if col3.button("🗑️", key=f"del_strat_{row['id']}"):
                    try:
                        _execute_query("DELETE FROM strategy_configs WHERE id = ?", (row['id'],))
                        st.success("Стратегия удалена.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка удаления: {e}")
    else:
        st.info("Нет настроенных стратегий.")


# --- MAIN RENDER ---

if __name__ == "__main__":
    render_bots_section()
    render_strategies_section()