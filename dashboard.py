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
from comparative_analyzer import ComparativeAnalyzer

# --- Конфигурация страницы Streamlit ---
st.set_page_config(
    page_title="Market Bots Dashboard",
    page_icon="🤖",
    layout="wide",
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

        first_trade = trades_df.iloc[0]
        strategy_name = first_trade['strategy_name']
        exchange = first_trade['exchange']
        instrument = first_trade['instrument']
        interval = first_trade['interval']
        risk_manager = first_trade['risk_manager']

        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval, f"{instrument}.parquet")
        if not os.path.exists(data_path):
            print(f"Warning: Data file not found for benchmark: {data_path}")
            return None
        historical_data = pd.read_parquet(data_path)

        analyzer = BacktestAnalyzer(
            trades_df=trades_df,
            historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            interval=interval,
            risk_manager_type=risk_manager
        )
        metrics = analyzer.calculate_metrics()

        return {
            "File": filename,
            "Exchange": exchange,
            "Strategy": strategy_name,
            "Instrument": instrument,
            "Interval": interval,
            "Risk Manager": risk_manager,
            "PnL (Strategy %)": float(metrics["Total PnL (Strategy)"].split(' ')[1].replace('(', '').replace('%)', '')),
            "PnL (B&H %)": float(metrics["Total PnL (Buy & Hold)"].split(' ')[1].replace('(', '').replace('%)', '')),
            "Win Rate (%)": float(metrics["Win Rate"].replace('%', '')),
            "Max Drawdown (%)": float(metrics["Max Drawdown"].replace('%', '')),
            "Profit Factor": float(metrics["Profit Factor"]),
            "Sharpe Ratio": float(metrics.get("Sharpe Ratio", 0.0)),
            "Total Trades": int(metrics["Total Trades"]),
        }
    except Exception as e:
        print(f"Warning: Could not process file {os.path.basename(file_path)}. Error: {e}")
        return None

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
            result_row = _process_single_backtest_file(file_path)
            if result_row:
                all_results.append(result_row)

    if not all_results:
        return pd.DataFrame()
    return pd.DataFrame(all_results)

# Функции для отрисовки графиков
def plot_equity_and_drawdown(analyzer: BacktestAnalyzer):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[0.7, 0.3])
    fig.add_trace(go.Scatter(x=analyzer.trades.index, y=analyzer.trades['equity_curve'],
                             mode='lines', name='Equity Curve'), row=1, col=1)
    benchmark_resampled = analyzer.benchmark_equity.reset_index(drop=True)
    benchmark_resampled.index = np.linspace(0, len(analyzer.trades) - 1, len(benchmark_resampled))
    fig.add_trace(go.Scatter(x=benchmark_resampled.index, y=benchmark_resampled.values,
                             mode='lines', name='Buy & Hold', line=dict(dash='dash', color='grey')), row=1, col=1)
    fig.add_trace(go.Scatter(x=analyzer.trades.index, y=analyzer.trades['drawdown_percent'],
                             mode='lines', name='Drawdown', fill='tozeroy', line_color='red'), row=2, col=1)
    fig.update_layout(title_text="Кривая капитала и просадки", height=600)
    fig.update_yaxes(title_text="Капитал", row=1, col=1)
    fig.update_yaxes(title_text="Просадка (%)", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)

def plot_pnl_distribution(analyzer: BacktestAnalyzer):
    fig = px.histogram(analyzer.trades, x="pnl", nbins=50,
                       title="Распределение PnL по сделкам",
                       labels={"pnl": "Прибыль/убыток по сделке"})
    st.plotly_chart(fig, use_container_width=True)

def plot_monthly_pnl(analyzer: BacktestAnalyzer):
    df = analyzer.trades.copy()
    df['exit_timestamp_utc'] = pd.to_datetime(df['exit_timestamp_utc'])
    df.set_index('exit_timestamp_utc', inplace=True)
    monthly_pnl = df['pnl'].resample('M').sum().reset_index()
    monthly_pnl['month'] = monthly_pnl['exit_timestamp_utc'].dt.strftime('%Y-%m')
    fig = px.bar(monthly_pnl, x='month', y='pnl',
                 title="Распределение PnL по месяцам",
                 labels={"pnl": "Месячный PnL", "month": "Месяц"},
                 color='pnl', color_continuous_scale=px.colors.diverging.RdYlGn)
    st.plotly_chart(fig, use_container_width=True)

def plot_trades_on_chart(historical_data: pd.DataFrame, trades_df: pd.DataFrame):
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=historical_data['time'], open=historical_data['open'], high=historical_data['high'],
        low=historical_data['low'], close=historical_data['close'], name='Свечи'
    ))
    trades_df['entry_timestamp_utc'] = pd.to_datetime(trades_df['entry_timestamp_utc'])
    trades_df['exit_timestamp_utc'] = pd.to_datetime(trades_df['exit_timestamp_utc'])
    long_trades = trades_df[trades_df['direction'] == 'BUY']
    short_trades = trades_df[trades_df['direction'] == 'SELL']
    fig.add_trace(go.Scatter(
        x=long_trades['entry_timestamp_utc'], y=long_trades['entry_price'],
        mode='markers', marker=dict(symbol='triangle-up', color='green', size=12), name='Вход в Лонг'
    ))
    fig.add_trace(go.Scatter(
        x=short_trades['entry_timestamp_utc'], y=short_trades['entry_price'],
        mode='markers', marker=dict(symbol='triangle-down', color='red', size=12), name='Вход в Шорт'
    ))
    tp_exits = trades_df[trades_df['exit_reason'] == 'Take Profit']
    sl_exits = trades_df[trades_df['exit_reason'] == 'Stop Loss']
    signal_exits = trades_df[trades_df['exit_reason'] == 'Signal']
    fig.add_trace(go.Scatter(
        x=tp_exits['exit_timestamp_utc'], y=tp_exits['exit_price'], mode='markers',
        marker=dict(symbol='circle', color='#2ca02c', size=10, line=dict(width=2, color='DarkSlateGrey')), name='Take Profit'
    ))
    fig.add_trace(go.Scatter(
        x=sl_exits['exit_timestamp_utc'], y=sl_exits['exit_price'], mode='markers',
        marker=dict(symbol='circle', color='#d62728', size=10, line=dict(width=2, color='DarkSlateGrey')), name='Stop Loss'
    ))
    fig.add_trace(go.Scatter(
        x=signal_exits['exit_timestamp_utc'], y=signal_exits['exit_price'],
        mode='markers', marker=dict(symbol='x', color='orange', size=10), name='Выход по сигналу'
    ))
    fig.update_layout(
        title_text="График сделок на свечах", xaxis_title="Время", yaxis_title="Цена",
        xaxis_rangeslider_visible=False, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)

def style_summary_table(df: pd.DataFrame):
    """
    Применяет продвинутую стилизацию к сводной таблице результатов.
    - Подсвечивает PnL (зеленый/красный).
    - Использует цветовые градиенты для ключевых метрик.
    - Подсвечивает лучшие/худшие значения.
    """
    # Задаем формат отображения для всех float колонок
    format_dict = {
        "PnL (Strategy %)": "{:.2f}%",
        "PnL (B&H %)": "{:.2f}%",
        "Win Rate (%)": "{:.2f}%",
        "Max Drawdown (%)": "{:.2f}%",
        "Profit Factor": "{:.2f}",
        "Sharpe Ratio": "{:.2f}",
    }

    # Применяем стили
    styler = df.style.format(format_dict, na_rep="-") \
        .background_gradient(cmap='Greens', subset=['PnL (Strategy %)', 'Profit Factor', 'Sharpe Ratio']) \
        .background_gradient(cmap='Reds_r', subset=['Max Drawdown (%)']) \
        .apply(lambda x: ['background-color: #d62728' if v < 0 else 'background-color: #2ca02c' for v in x],
               subset=['PnL (Strategy %)']) \
        .highlight_max(subset=['PnL (Strategy %)', 'Win Rate (%)', 'Profit Factor', 'Sharpe Ratio'], color='#5fba7d') \
        .highlight_min(subset=['Max Drawdown (%)'], color='#d62728')

    return styler


def _render_mode1_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """Отрисовывает UI для режима 1: Стратегия vs Стратегия."""
    st.subheader("1. Сравнение стратегий на одном инструменте")

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_instrument = st.selectbox("Инструмент:", summary_df["Instrument"].unique(), key="c1_instr")
    with col2:
        selected_interval = st.selectbox("Интервал:", summary_df["Interval"].unique(), key="c1_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", summary_df["Risk Manager"].unique(), key="c1_rm")

    selected_strategies = st.multiselect("Выберите стратегии для сравнения:", summary_df["Strategy"].unique(),
                                         key="c1_strats")

    if st.button("Сравнить стратегии", key="c1_btn"):
        if len(selected_strategies) < 2:
            st.warning("Пожалуйста, выберите хотя бы две стратегии.")
        else:
            with st.spinner("Выполняется сравнение..."):
                metrics_df, fig = comp_analyzer.compare_strategies_on_instrument(
                    strategy_names=selected_strategies, instrument=selected_instrument,
                    interval=selected_interval, risk_manager=selected_rm
                )
                st.dataframe(metrics_df.style.format("{:.2f}"))
                st.plotly_chart(fig, use_container_width=True)


def _render_mode2_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """Отрисовывает UI для режима 2: Анализ робастности."""
    st.subheader("2. Анализ робастности стратегии")

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_strategy = st.selectbox("Стратегия:", summary_df["Strategy"].unique(), key="c2_strat")
    with col2:
        selected_interval = st.selectbox("Интервал:", summary_df["Interval"].unique(), key="c2_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", summary_df["Risk Manager"].unique(), key="c2_rm")

    selected_instruments = st.multiselect("Выберите инструменты для портфеля:", summary_df["Instrument"].unique(),
                                          key="c2_instrs")

    if st.button("Анализировать робастность", key="c2_btn"):
        if len(selected_instruments) < 2:
            st.warning("Пожалуйста, выберите хотя бы два инструмента.")
        else:
            with st.spinner("Выполняется анализ..."):
                metrics_df, fig = comp_analyzer.analyze_instrument_robustness(
                    strategy_name=selected_strategy, instruments=selected_instruments,
                    interval=selected_interval, risk_manager=selected_rm
                )
                st.dataframe(metrics_df.style.format(subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                                                     formatter="{:.2f}"))
                st.plotly_chart(fig, use_container_width=True)


def _render_mode3_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """Отрисовывает UI для режима 3: Портфель vs Портфель."""
    st.subheader("3. Сравнение портфельных результатов")

    col1, col2 = st.columns(2)
    with col1:
        selected_interval = st.selectbox("Общий интервал:", summary_df["Interval"].unique(), key="c3_interval")
    with col2:
        selected_rm = st.selectbox("Общий риск-менеджер:", summary_df["Risk Manager"].unique(), key="c3_rm")

    selected_strategies = st.multiselect("Выберите стратегии:", summary_df["Strategy"].unique(), key="c3_strats")
    selected_instruments = st.multiselect("Выберите инструменты для портфеля:", summary_df["Instrument"].unique(),
                                          key="c3_instrs")

    if st.button("Сравнить портфели", key="c3_btn"):
        if len(selected_strategies) < 2 or len(selected_instruments) < 2:
            st.warning("Пожалуйста, выберите хотя бы 2 стратегии и 2 инструмента.")
        else:
            with st.spinner("Выполняется сравнение портфелей..."):
                metrics_df, fig = comp_analyzer.compare_aggregated_strategies(
                    strategy_names=selected_strategies, instruments=selected_instruments,
                    interval=selected_interval, risk_manager=selected_rm
                )
                st.dataframe(metrics_df.style.format(subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                                                     formatter="{:.2f}"))
                st.plotly_chart(fig, use_container_width=True)

def render_detailed_analysis_section(filtered_df: pd.DataFrame):
    """Отрисовывает секцию детального анализа для УЖЕ отфильтрованных данных."""
    st.header("Детальный анализ отдельного бэктеста")

    if filtered_df.empty:
        st.warning("По выбранным фильтрам не найдено ни одного бэктеста для детального анализа.")
        return

    selected_file = st.selectbox("Выберите бэктест для детального анализа:", options=filtered_df["File"].tolist())
    if selected_file:
        trades_df = load_trades_from_file(os.path.join(PATH_CONFIG["LOGS_DIR"], selected_file))
        row = filtered_df[filtered_df["File"] == selected_file].iloc[0]
        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], row["Exchange"], row["Interval"],
                                 f"{row['Instrument']}.parquet")
        historical_data = pd.read_parquet(data_path)
        analyzer = BacktestAnalyzer(
            trades_df=trades_df, historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            interval=row["Interval"], risk_manager_type=row["Risk Manager"]
        )
        analyzer.trades['drawdown_percent'] = (analyzer.trades['equity_curve'] / analyzer.trades[
            'equity_curve'].cummax() - 1) * 100

        tab1, tab2, tab3 = st.tabs(["📈 Кривая капитала", "📊 Анализ PnL", "🕯️ График сделок"])
        with tab1: plot_equity_and_drawdown(analyzer)
        with tab2: plot_pnl_distribution(analyzer); plot_monthly_pnl(analyzer)
        with tab3: plot_trades_on_chart(historical_data, trades_df)


def render_comparative_analysis_section(summary_df: pd.DataFrame):
    """Отрисовывает UI для секции сравнительного анализа."""
    st.divider()
    st.header("🔬 Сравнительный анализ")
    comp_analyzer = ComparativeAnalyzer(summary_df)
    comparison_mode = st.radio(
        "Выберите режим сравнения:",
        ["1. Стратегия vs Стратегия", "2. Анализ робастности", "3. Портфель vs Портфель"],
        horizontal=True
    )
    st.markdown("---")
    if "1." in comparison_mode:
        _render_mode1_ui(comp_analyzer, summary_df)
    elif "2." in comparison_mode:
        _render_mode2_ui(comp_analyzer, summary_df)
    elif "3." in comparison_mode:
        _render_mode3_ui(comp_analyzer, summary_df)

# Основная часть приложения
def main():
    st.title("🤖 Панель анализа торговых стратегий")

    summary_df = load_all_backtests(PATH_CONFIG["LOGS_DIR"])

    if summary_df.empty:
        st.warning("Не найдено ни одного файла с результатами бэктестов (`_trades.jsonl`) в папке `logs/`.")
        st.info("Запустите бэктест, чтобы сгенерировать результаты.")
        return

    # --- Боковая панель с фильтрами ---
    st.sidebar.header("Фильтры")
    selected_exchanges = st.sidebar.multiselect("Биржи", options=summary_df["Exchange"].unique(), default=summary_df["Exchange"].unique())
    selected_strategies = st.sidebar.multiselect("Стратегии", options=summary_df["Strategy"].unique(), default=summary_df["Strategy"].unique())
    selected_instruments = st.sidebar.multiselect("Инструменты", options=summary_df["Instrument"].unique(), default=summary_df["Instrument"].unique())
    selected_rms = st.sidebar.multiselect("Риск-менеджеры", options=summary_df["Risk Manager"].unique(), default=summary_df["Risk Manager"].unique())

    # Применяем фильтры один раз
    filtered_df = summary_df[
        (summary_df["Exchange"].isin(selected_exchanges)) &
        (summary_df["Strategy"].isin(selected_strategies)) &
        (summary_df["Instrument"].isin(selected_instruments)) &
        (summary_df["Risk Manager"].isin(selected_rms))
    ]

    # --- Отображение сводной таблицы ---
    st.header("Сводная таблица результатов")
    st.dataframe(style_summary_table(filtered_df), use_container_width=True)

    # --- Отображение секций анализа ---
    render_detailed_analysis_section(filtered_df) # Передаем отфильтрованные данные
    render_comparative_analysis_section(summary_df) # Сравнительный анализ работает со всеми данными

if __name__ == "__main__":
    main()