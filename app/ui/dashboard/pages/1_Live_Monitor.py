import streamlit as st
import pandas as pd
import sqlite3
import time
import os
from config import BASE_DIR

# Настройка страницы
st.set_page_config(page_title="Live Monitor", page_icon="🚀", layout="wide")

st.title("🚀 Live Signal Monitor")

DB_PATH = os.path.join(BASE_DIR, "market_bots.db")


def load_data():
    """Читает данные из SQLite напрямую в Pandas DF."""
    con = sqlite3.connect(DB_PATH)

    # 1. Активные Боты
    bots = pd.read_sql("""
        SELECT id, name, is_active, created_at 
        FROM bot_instances
    """, con)

    # 2. Активные Стратегии
    strats = pd.read_sql("""
        SELECT s.id, s.strategy_name, s.exchange, s.instrument, s.interval, 
               b.name as bot_name, s.is_active
        FROM strategy_configs s
        LEFT JOIN bot_instances b ON s.bot_id = b.id
        WHERE s.is_active = 1
    """, con)

    # 3. Последние Сигналы (последние 20)
    signals = pd.read_sql("""
        SELECT timestamp, exchange, instrument, strategy_name, direction, price
        FROM signal_logs
        ORDER BY timestamp DESC
        LIMIT 20
    """, con)

    # 4. Подписчики
    subs = pd.read_sql("""
        SELECT b.name as bot_name, COUNT(t.id) as sub_count
        FROM telegram_subscribers t
        JOIN bot_instances b ON t.bot_id = b.id
        WHERE t.is_active = 1
        GROUP BY b.name
    """, con)

    con.close()
    return bots, strats, signals, subs


# Кнопка обновления
if st.button('🔄 Обновить данные'):
    st.rerun()

# Автообновление (экспериментально, лучше кнопкой, но можно включить)
# time.sleep(1)
# st.rerun()

try:
    df_bots, df_strats, df_signals, df_subs = load_data()

    # --- МЕТРИКИ ВЕРХНЕГО УРОВНЯ ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Активных ботов", len(df_bots[df_bots['is_active'] == 1]))
    col2.metric("Активных стратегий", len(df_strats))
    col3.metric("Всего подписчиков", df_subs['sub_count'].sum() if not df_subs.empty else 0)

    last_sig_time = df_signals.iloc[0]['timestamp'] if not df_signals.empty else "Нет данных"
    col4.metric("Последний сигнал", str(last_sig_time).split('.')[0])

    st.markdown("---")

    # --- ДЕТАЛИЗАЦИЯ ---
    c1, c2 = st.columns([2, 1])

    with c1:
        st.subheader("📡 Лента Сигналов")
        if not df_signals.empty:
            # Красивое форматирование таблицы
            def highlight_direction(val):
                color = '#d62728' if val == 'SELL' else '#2ca02c'
                return f'color: {color}; font-weight: bold'


            st.dataframe(
                df_signals.style.applymap(highlight_direction, subset=['direction']),
                use_container_width=True,
                height=400
            )
        else:
            st.info("Сигналов пока нет.")

    with c2:
        st.subheader("🤖 Статус Ботов")
        if not df_subs.empty:
            st.dataframe(df_subs, use_container_width=True, hide_index=True)

        st.subheader("⚙️ Активные Пары")
        if not df_strats.empty:
            st.dataframe(
                df_strats[['bot_name', 'instrument', 'interval', 'strategy_name']],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.warning("Нет активных стратегий!")

except Exception as e:
    st.error(f"Ошибка подключения к БД: {e}")
    st.info("Убедитесь, что файл market_bots.db существует и инициализирован.")