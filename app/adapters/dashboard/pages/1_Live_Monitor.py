"""
Страница мониторинга сигналов (Live Monitor).

Этот модуль визуализирует текущее состояние системы:
- Список активных ботов и стратегий.
- Ленту последних сгенерированных сигналов.
- Статистику подписчиков.

Данные читаются напрямую из базы данных SQLite, что обеспечивает
отображение информации в реальном времени (при обновлении страницы).
"""

import sqlite3
import pandas as pd
import streamlit as st

from app.shared.config import config

# Глобальные настройки путей
DB_PATH = config.DB_PATH

# Настройка страницы Streamlit
st.set_page_config(
    page_title="Live Monitor",
    page_icon="🚀",
    layout="wide"
)
st.title("🚀 Live Signal Monitor")


def load_data():
    """
    Загружает оперативные данные из базы данных.

    Выполняет несколько SQL-запросов для получения сводной информации
    о ботах, стратегиях, сигналах и подписчиках.

    Returns:
        tuple: Кортеж из четырех DataFrame (bots, strategies, signals, subscribers).
    """
    con = sqlite3.connect(DB_PATH)

    try:
        # 1. Активные Боты
        bots = pd.read_sql("""
            SELECT id, name, is_active, created_at 
            FROM bot_instances
        """, con)

        # 2. Активные Стратегии (с джойном на имя бота)
        strats = pd.read_sql("""
            SELECT s.id, s.strategy_name, s.exchange, s.instrument, s.interval, 
                   b.name as bot_name, s.is_active
            FROM strategy_configs s
            LEFT JOIN bot_instances b ON s.bot_id = b.id
            WHERE s.is_active = 1
        """, con)

        # 3. Последние Сигналы (лимит 20 для производительности)
        signals = pd.read_sql("""
            SELECT timestamp, exchange, instrument, strategy_name, direction, price
            FROM signal_logs
            ORDER BY timestamp DESC
            LIMIT 20
        """, con)

        # 4. Статистика подписчиков по ботам
        subs = pd.read_sql("""
            SELECT b.name as bot_name, COUNT(t.id) as sub_count
            FROM telegram_subscribers t
            JOIN bot_instances b ON t.bot_id = b.id
            WHERE t.is_active = 1
            GROUP BY b.name
        """, con)

        return bots, strats, signals, subs

    except pd.errors.DatabaseError:
        # Если таблицы еще не созданы
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    finally:
        con.close()


def highlight_direction(val):
    """
    Функция стилизации ячеек таблицы сигналов.
    Окрашивает 'BUY' в зеленый, 'SELL' в красный.
    """
    color = '#d62728' if val == 'SELL' else '#2ca02c'
    return f'color: {color}; font-weight: bold'


# --- Основной UI ---

# Кнопка ручного обновления
if st.button('🔄 Обновить данные'):
    st.rerun()

try:
    df_bots, df_strats, df_signals, df_subs = load_data()

    # --- СЕКЦИЯ 1: МЕТРИКИ (KPI) ---
    col1, col2, col3, col4 = st.columns(4)

    active_bots_count = len(df_bots[df_bots['is_active'] == 1]) if not df_bots.empty else 0
    col1.metric("Активных ботов", active_bots_count)

    col2.metric("Активных стратегий", len(df_strats))

    total_subs = df_subs['sub_count'].sum() if not df_subs.empty else 0
    col3.metric("Всего подписчиков", total_subs)

    last_sig_time = "Нет данных"
    if not df_signals.empty:
        # Убираем микросекунды для красоты
        last_sig_time = str(df_signals.iloc[0]['timestamp']).split('.')[0]

    col4.metric("Последний сигнал", last_sig_time)

    st.markdown("---")

    # --- СЕКЦИЯ 2: ТАБЛИЦЫ ---
    c1, c2 = st.columns([2, 1])

    with c1:
        st.subheader("📡 Лента Сигналов")
        if not df_signals.empty:
            # Применяем стили к DataFrame
            styled_df = df_signals.style.map(highlight_direction, subset=['direction'])

            st.dataframe(
                styled_df,
                use_container_width=True,
                height=400
            )
        else:
            st.info("Лента сигналов пуста. Запустите 'run_signals.py', чтобы начать мониторинг.")

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
            st.warning("Нет активных стратегий! Добавьте их на вкладке Configuration.")

except Exception as e:
    st.error(f"Ошибка загрузки данных: {e}")
    st.info("Убедитесь, что база данных инициализирована (скрипт init_db.py).")