import os
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from typing import Dict, Any, Optional
from utils.file_io import load_trades_from_file
from analyzer import BacktestAnalyzer
from config import PATH_CONFIG, BACKTEST_CONFIG

# --- Конфигурация страницы Streamlit ---
st.set_page_config(
    page_title="Market Bots Dashboard",
    page_icon="🤖",
    layout="wide",  # Используем всю ширину экрана
)

def _process_single_backtest_file(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Обрабатывает один .jsonl файл с результатами бэктеста.
    Возвращает словарь с ключевыми метриками или None в случае ошибки.
    """
    try:
        filename = os.path.basename(file_path)
        trades_df = load_trades_from_file(file_path)
        if trades_df.empty:
            return None

        # 1. Парсим метаданные из имени файла
        parts = filename.replace('_trades.jsonl', '').split('_')
        strategy_name = parts[2]
        instrument = parts[3]
        interval = parts[4]
        risk_manager = parts[5].replace('RM-', '')

        # 2. Загружаем исторические данные для расчета бенчмарка
        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], interval, f"{instrument}.parquet")
        if not os.path.exists(data_path):
            print(f"Warning: Data file not found for benchmark: {data_path}")
            return None
        historical_data = pd.read_parquet(data_path)

        # 3. Создаем анализатор и рассчитываем метрики
        analyzer = BacktestAnalyzer(
            trades_df=trades_df,
            historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            interval=interval,
            risk_manager_type=risk_manager
        )
        metrics = analyzer.calculate_metrics()

        # 4. Формируем и возвращаем итоговый словарь
        return {
            "File": filename,
            "Strategy": strategy_name,
            "Instrument": instrument,
            "Interval": interval,
            "Risk Manager": risk_manager,
            "PnL (Strategy %)": float(metrics["Total PnL (Strategy)"].split(' ')[1].replace('(', '').replace('%)', '')),
            "PnL (B&H %)": float(metrics["Total PnL (Buy & Hold)"].split(' ')[1].replace('(', '').replace('%)', '')),
            "Win Rate (%)": float(metrics["Win Rate"].replace('%', '')),
            "Max Drawdown (%)": float(metrics["Max Drawdown"].replace('%', '')),
            "Profit Factor": float(metrics["Profit Factor"]),
            "Total Trades": int(metrics["Total Trades"]),
        }
    except Exception as e:
        print(f"Warning: Could not process file {os.path.basename(file_path)}. Error: {e}")
        return None

# --- Кэшированная функция для загрузки данных ---
# @st.cache_data говорит Streamlit'у выполнять эту функцию только один раз,
# если входные параметры не изменились. Это КЛЮЧЕВОЙ элемент для производительности.
@st.cache_data
def load_all_backtests(logs_dir: str) -> pd.DataFrame:
    """
    Сканирует директорию с логами, делегируя обработку каждого файла
    helper-функции, и возвращает итоговый DataFrame.
    """
    all_results = []
    if not os.path.isdir(logs_dir):
        return pd.DataFrame()

    for filename in os.listdir(logs_dir):
        if filename.endswith("_trades.jsonl"):
            file_path = os.path.join(logs_dir, filename)
            # Делегируем всю сложную работу helper'у
            result_row = _process_single_backtest_file(file_path)
            if result_row:
                all_results.append(result_row)

    return pd.DataFrame(all_results)


# --- Функции для отрисовки графиков ---
def plot_equity_and_drawdown(analyzer: BacktestAnalyzer):
    """Рисует интерактивный график капитала и просадок."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[0.7, 0.3])

    # График капитала
    fig.add_trace(go.Scatter(x=analyzer.trades.index, y=analyzer.trades['equity_curve'],
                             mode='lines', name='Equity Curve'), row=1, col=1)

    # График Buy & Hold
    benchmark_resampled = analyzer.benchmark_equity.reset_index(drop=True)
    benchmark_resampled.index = np.linspace(0, len(analyzer.trades) - 1, len(benchmark_resampled))
    fig.add_trace(go.Scatter(x=benchmark_resampled.index, y=benchmark_resampled.values,
                             mode='lines', name='Buy & Hold', line=dict(dash='dash', color='grey')), row=1, col=1)

    # График просадок (Underwater Plot)
    fig.add_trace(go.Scatter(x=analyzer.trades.index, y=analyzer.trades['drawdown_percent'],
                             mode='lines', name='Drawdown', fill='tozeroy', line_color='red'), row=2, col=1)

    fig.update_layout(title_text="Кривая капитала и просадки", height=600)
    fig.update_yaxes(title_text="Капитал", row=1, col=1)
    fig.update_yaxes(title_text="Просадка (%)", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)


def plot_pnl_distribution(analyzer: BacktestAnalyzer):
    """Рисует гистограмму распределения PnL по сделкам."""
    fig = px.histogram(analyzer.trades, x="pnl", nbins=50,
                       title="Распределение PnL по сделкам",
                       labels={"pnl": "Прибыль/убыток по сделке"})
    st.plotly_chart(fig, use_container_width=True)


def plot_monthly_pnl(analyzer: BacktestAnalyzer):
    """Рисует столбчатую диаграмму PnL по месяцам."""
    # Убедимся, что 'timestamp_utc' - это datetime объект и установим его как индекс
    df = analyzer.trades.copy()
    df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'])
    df.set_index('timestamp_utc', inplace=True)

    monthly_pnl = df['pnl'].resample('M').sum().reset_index()
    monthly_pnl['month'] = monthly_pnl['timestamp_utc'].dt.strftime('%Y-%m')

    fig = px.bar(monthly_pnl, x='month', y='pnl',
                 title="Распределение PnL по месяцам",
                 labels={"pnl": "Месячный PnL", "month": "Месяц"},
                 color='pnl', color_continuous_scale=px.colors.diverging.RdYlGn)
    st.plotly_chart(fig, use_container_width=True)


# --- Основная часть приложения ---
st.title("🤖 Панель анализа торговых стратегий")

# Загружаем данные
summary_df = load_all_backtests(PATH_CONFIG["LOGS_DIR"])

if summary_df.empty:
    st.warning("Не найдено ни одного файла с результатами бэктестов (`_trades.jsonl`) в папке `logs/`.")
    st.info("Запустите бэктест с помощью `run.py` или `batch_tester.py`, чтобы сгенерировать результаты.")
else:
    # --- Боковая панель с фильтрами ---
    st.sidebar.header("Фильтры")

    selected_strategies = st.sidebar.multiselect(
        "Стратегии",
        options=summary_df["Strategy"].unique(),
        default=summary_df["Strategy"].unique()
    )
    selected_instruments = st.sidebar.multiselect(
        "Инструменты",
        options=summary_df["Instrument"].unique(),
        default=summary_df["Instrument"].unique()
    )
    selected_rms = st.sidebar.multiselect(
        "Риск-менеджеры",
        options=summary_df["Risk Manager"].unique(),
        default=summary_df["Risk Manager"].unique()
    )

    # Применяем фильтры
    filtered_df = summary_df[
        (summary_df["Strategy"].isin(selected_strategies)) &
        (summary_df["Instrument"].isin(selected_instruments)) &
        (summary_df["Risk Manager"].isin(selected_rms))
        ]

    # --- Основной экран ---
    st.header("Сводная таблица результатов")
    st.dataframe(filtered_df.style.format({
        "PnL (Strategy %)": "{:.2f}%",
        "PnL (B&H %)": "{:.2f}%",
        "Win Rate (%)": "{:.2f}%",
        "Max Drawdown (%)": "{:.2f}%",
        "Profit Factor": "{:.2f}",
    }), use_container_width=True)

    st.header("Детальный анализ бэктеста")

    # Выпадающий список для выбора конкретного бэктеста из отфильтрованных
    selected_file = st.selectbox(
        "Выберите бэктест для детального анализа:",
        options=filtered_df["File"].tolist()
    )

    if selected_file:
        # Загружаем данные для выбранного файла
        trades_df = load_trades_from_file(os.path.join(PATH_CONFIG["LOGS_DIR"], selected_file))

        # Создаем экземпляр анализатора для выбранного бэктеста
        row = filtered_df[filtered_df["File"] == selected_file].iloc[0]

        #  Загружаем исторические данные так же, как мы это делали в load_all_backtests
        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], row["Interval"], f"{row['Instrument']}.parquet")
        historical_data = pd.read_parquet(data_path)

        analyzer = BacktestAnalyzer(
            trades_df=trades_df,
            historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            interval=row["Interval"],
            risk_manager_type=row["Risk Manager"]
        )

        # Дополнительно рассчитаем просадку в % для графика
        analyzer.trades['drawdown_percent'] = (analyzer.trades['equity_curve'] / analyzer.trades[
            'equity_curve'].cummax() - 1) * 100

        # Используем вкладки для организации графиков
        tab1, tab2 = st.tabs(["📈 Кривая капитала и просадки", "📊 Анализ PnL"])

        with tab1:
            plot_equity_and_drawdown(analyzer)

        with tab2:
            plot_pnl_distribution(analyzer)
            plot_monthly_pnl(analyzer)