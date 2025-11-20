import streamlit as st
import pandas as pd
import sqlite3
import os
from config import BASE_DIR
from app.strategies import AVAILABLE_STRATEGIES
from config import EXCHANGE_INTERVAL_MAPS

st.set_page_config(page_title="Configuration", page_icon="⚙️", layout="wide")
st.title("⚙️ Управление Конфигурацией")

DB_PATH = os.path.join(BASE_DIR, "market_bots.db")


def get_connection():
    return sqlite3.connect(DB_PATH)


# --- РАЗДЕЛ 1: БОТЫ ---
st.header("🤖 Телеграм Боты")

with st.expander("Добавить нового бота"):
    with st.form("add_bot_form"):
        new_bot_name = st.text_input("Имя бота (внутреннее)")
        new_bot_token = st.text_input("Токен (от BotFather)", type="password")
        submitted_bot = st.form_submit_button("Создать бота")

        if submitted_bot and new_bot_name and new_bot_token:
            try:
                con = get_connection()
                cur = con.cursor()
                cur.execute("INSERT INTO bot_instances (name, token, is_active) VALUES (?, ?, 1)",
                            (new_bot_name, new_bot_token))
                con.commit()
                con.close()
                st.success(f"Бот {new_bot_name} добавлен!")
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка: {e}")

# Таблица ботов
con = get_connection()
bots_df = pd.read_sql("SELECT id, name, is_active FROM bot_instances", con)
con.close()

if not bots_df.empty:
    # Редактирование статуса ботов
    for index, row in bots_df.iterrows():
        col1, col2, col3 = st.columns([3, 1, 1])
        col1.write(f"**{row['name']}** (ID: {row['id']})")

        is_active = col2.toggle("Active", value=bool(row['is_active']), key=f"bot_toggle_{row['id']}")

        if is_active != bool(row['is_active']):
            con = get_connection()
            con.execute("UPDATE bot_instances SET is_active = ? WHERE id = ?", (is_active, row['id']))
            con.commit()
            con.close()
            st.toast(f"Статус бота {row['name']} обновлен!")

        if col3.button("🗑️", key=f"del_bot_{row['id']}"):
            con = get_connection()
            con.execute("DELETE FROM bot_instances WHERE id = ?", (row['id'],))
            con.commit()
            con.close()
            st.rerun()
else:
    st.info("Нет добавленных ботов.")

st.divider()

# --- РАЗДЕЛ 2: СТРАТЕГИИ ---
st.header("📈 Торговые Стратегии")

# Форма добавления
with st.expander("Добавить новую стратегию", expanded=True):
    if bots_df.empty:
        st.warning("Сначала добавьте хотя бы одного бота!")
    else:
        with st.form("add_strat_form"):
            c1, c2 = st.columns(2)
            selected_bot_name = c1.selectbox("Привязать к боту", bots_df['name'].tolist())
            selected_strategy = c2.selectbox("Класс стратегии", list(AVAILABLE_STRATEGIES.keys()))

            c3, c4, c5 = st.columns(3)
            exchange = c3.selectbox("Биржа", ["bybit", "tinkoff"])
            instrument = c4.text_input("Инструмент (Ticker)", value="BTCUSDT").upper()

            # Динамические интервалы
            intervals = list(EXCHANGE_INTERVAL_MAPS.get(exchange, {}).keys())
            interval = c5.selectbox("Таймфрейм", intervals if intervals else ["1min"])

            submitted_strat = st.form_submit_button("Добавить стратегию")

            if submitted_strat:
                bot_id = bots_df[bots_df['name'] == selected_bot_name].iloc[0]['id']
                # Простейшие дефолтные параметры (в будущем можно сделать JSON редактор)
                default_params = "{}"

                try:
                    con = get_connection()
                    con.execute("""
                        INSERT INTO strategy_configs 
                        (bot_id, exchange, instrument, interval, strategy_name, parameters, is_active, risk_manager_type)
                        VALUES (?, ?, ?, ?, ?, ?, 1, 'FIXED')
                    """, (int(bot_id), exchange, instrument, interval, selected_strategy, default_params))
                    con.commit()
                    con.close()
                    st.success("Стратегия добавлена в мониторинг!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка БД: {e}")

# Таблица стратегий
con = get_connection()
strats_df = pd.read_sql("""
    SELECT s.id, s.exchange, s.instrument, s.interval, s.strategy_name, s.is_active, b.name as bot_name
    FROM strategy_configs s
    LEFT JOIN bot_instances b ON s.bot_id = b.id
""", con)
con.close()

if not strats_df.empty:
    st.dataframe(strats_df, use_container_width=True, hide_index=True)

    st.subheader("Управление активностью")
    # Упрощенный список для тогликов
    for index, row in strats_df.iterrows():
        col1, col2, col3 = st.columns([4, 1, 1])
        label = f"{row['exchange']} {row['instrument']} ({row['interval']}) - {row['strategy_name']} -> {row['bot_name']}"
        col1.write(label)

        is_active = col2.toggle("On/Off", value=bool(row['is_active']), key=f"strat_toggle_{row['id']}")

        if is_active != bool(row['is_active']):
            con = get_connection()
            con.execute("UPDATE strategy_configs SET is_active = ? WHERE id = ?", (is_active, row['id']))
            con.commit()
            con.close()
            st.toast("Статус стратегии обновлен.")

        if col3.button("🗑️", key=f"del_strat_{row['id']}"):
            con = get_connection()
            con.execute("DELETE FROM strategy_configs WHERE id = ?", (row['id'],))
            con.commit()
            con.close()
            st.rerun()
else:
    st.info("Нет активных стратегий.")