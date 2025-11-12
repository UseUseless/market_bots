import os
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from typing import Dict, Any, Optional, List, Tuple

from app.utils.file_io import load_trades_from_file
from app.analyzers.single_run_analyzer import SingleRunAnalyzer
from config import PATH_CONFIG, BACKTEST_CONFIG
from app.analyzers.comparative_analyzer import ComparativeAnalyzer

# --- Конфигурация страницы Streamlit ---
st.set_page_config(
    page_title="Market Bots Dashboard",
    page_icon="🤖",
    layout="wide",
)

def _process_single_backtest_file(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Обрабатывает один .jsonl файл с результатами бэктеста.
    Возвращает словарь с ключевыми метриками или словарь с ошибкой.
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

        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval, f"{instrument.upper()}.parquet")
        if not os.path.exists(data_path):
            raise FileNotFoundError(f"Файл данных не найден: {data_path}")

        historical_data = pd.read_parquet(data_path)

        analyzer = SingleRunAnalyzer(
            trades_df=trades_df,
            historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            interval=interval,
            risk_manager_type=risk_manager,
            exchange=exchange
        )
        metrics = analyzer.calculate_metrics()

        pnl_strategy_str = metrics["Total PnL (Strategy)"].split(' ')[1].replace('(', '').replace('%)', '')
        pnl_bh_str = metrics["Total PnL (Buy & Hold)"].split(' ')[1].replace('(', '').replace('%)', '')
        win_rate_str = metrics["Win Rate"].replace('%', '')
        max_drawdown_str = metrics["Max Drawdown"].replace('%', '')
        sharpe_str = metrics["Sharpe Ratio"].split(' ')[0]

        return {
            "File": filename,
            "Exchange": exchange,
            "Strategy": strategy_name,
            "Instrument": instrument,
            "Interval": interval,
            "Risk Manager": risk_manager,
            "PnL (Strategy %)": float(pnl_strategy_str),
            "PnL (B&H %)": float(pnl_bh_str),
            "Win Rate (%)": float(win_rate_str),
            "Max Drawdown (%)": float(max_drawdown_str),
            "Profit Factor": float(metrics["Profit Factor"]) if metrics["Profit Factor"] != 'inf' else np.inf,
            "Sharpe Ratio": float(sharpe_str),
            "Total Trades": int(metrics["Total Trades"]),
        }
    except Exception as e:
        return {"error": f"Не удалось обработать файл {os.path.basename(file_path)}: {e}"}


def _render_portfolio_selector_pane(pane_title: str, key_prefix: str, summary_df: pd.DataFrame) -> Optional[
    Dict[str, Any]]:
    """Отрисовывает одну колонку для выбора параметров портфеля."""
    st.subheader(pane_title)

    selected_strategy = st.selectbox("Стратегия:", summary_df["Strategy"].unique(), key=f"{key_prefix}_strat")
    selected_interval = st.selectbox("Интервал:", summary_df["Interval"].unique(), key=f"{key_prefix}_interval")
    selected_rm = st.selectbox("Риск-менеджер:", summary_df["Risk Manager"].unique(), key=f"{key_prefix}_rm")

    available_instruments = sorted(summary_df[
                                       (summary_df['Strategy'] == selected_strategy) &
                                       (summary_df['Interval'] == selected_interval) &
                                       (summary_df['Risk Manager'] == selected_rm)
                                       ]['Instrument'].unique())

    if not available_instruments:
        st.warning("Нет данных для этой комбинации.")
        return None

    select_all = st.checkbox("Выбрать все", key=f"{key_prefix}_select_all")

    if select_all:
        selected_instruments = st.multiselect(
            "Инструменты:",
            options=available_instruments,
            default=available_instruments,
            key=f"{key_prefix}_instrs_all"
        )
    else:
        selected_instruments = st.multiselect(
            "Инструменты:",
            options=available_instruments,
            default=[],
            key=f"{key_prefix}_instrs_manual"
        )

    if not selected_instruments:
        st.info("Выберите инструменты для портфеля.")
        return None

    return {
        "strategy": selected_strategy,
        "interval": selected_interval,
        "rm": selected_rm,
        "instruments": selected_instruments
    }


@st.cache_data
def load_all_backtests(logs_dir: str) -> Tuple[pd.DataFrame, List[str]]:
    """
    Сканирует директорию с логами, обрабатывает каждый файл и возвращает
    итоговый DataFrame, а также список файлов, которые не удалось обработать.
    """
    all_results = []
    failed_files = []
    if not os.path.isdir(logs_dir):
        return pd.DataFrame(), ["Директория логов не найдена"]

    for filename in os.listdir(logs_dir):
        if filename.endswith("_trades.jsonl"):
            file_path = os.path.join(logs_dir, filename)
            result_row = _process_single_backtest_file(file_path)

            if result_row and "error" not in result_row:
                all_results.append(result_row)
            elif result_row and "error" in result_row:
                failed_files.append(result_row["error"])

    if not all_results:
        return pd.DataFrame(), failed_files

    return pd.DataFrame(all_results), failed_files


def plot_equity_and_drawdown(analyzer: SingleRunAnalyzer):
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[0.7, 0.3])

    trades_df = analyzer.calculator.trades

    fig.add_trace(go.Scatter(x=trades_df.index, y=trades_df['equity_curve'],
                             mode='lines', name='Equity Curve'), row=1, col=1)

    benchmark_resampled = analyzer.benchmark_equity.reset_index(drop=True)
    benchmark_resampled.index = np.linspace(0, len(trades_df) - 1, len(benchmark_resampled))

    fig.add_trace(go.Scatter(x=benchmark_resampled.index, y=benchmark_resampled.values,
                             mode='lines', name='Buy & Hold', line=dict(dash='dash', color='grey')), row=1, col=1)

    fig.add_trace(go.Scatter(x=trades_df.index, y=trades_df['drawdown_percent'],
                             mode='lines', name='Drawdown', fill='tozeroy', line_color='red'), row=2, col=1)

    fig.update_layout(title_text="Кривая капитала и просадки", height=600)
    fig.update_yaxes(title_text="Капитал", row=1, col=1)
    fig.update_yaxes(title_text="Просадка (%)", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)


def plot_pnl_distribution(analyzer: SingleRunAnalyzer):
    fig = px.histogram(analyzer.calculator.trades, x="pnl", nbins=50,
                       title="Распределение PnL по сделкам",
                       labels={"pnl": "Прибыль/убыток по сделке"})
    st.plotly_chart(fig, use_container_width=True)


def plot_monthly_pnl(analyzer: SingleRunAnalyzer):
    df = analyzer.calculator.trades.copy()
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
        marker=dict(symbol='circle', color='#2ca02c', size=10, line=dict(width=2, color='DarkSlateGrey')),
        name='Take Profit'
    ))
    fig.add_trace(go.Scatter(
        x=sl_exits['exit_timestamp_utc'], y=sl_exits['exit_price'], mode='markers',
        marker=dict(symbol='circle', color='#d62728', size=10, line=dict(width=2, color='DarkSlateGrey')),
        name='Stop Loss'
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
    format_dict = {
        "PnL (Strategy %)": "{:.2f}%", "PnL (B&H %)": "{:.2f}%",
        "Win Rate (%)": "{:.2f}%", "Max Drawdown (%)": "{:.2f}%",
        "Profit Factor": "{:.2f}", "Sharpe Ratio": "{:.2f}",
    }
    styler = df.style.format(format_dict, na_rep="-") \
        .background_gradient(cmap='Greens', subset=['PnL (Strategy %)', 'Profit Factor', 'Sharpe Ratio']) \
        .background_gradient(cmap='Reds_r', subset=['Max Drawdown (%)']) \
        .apply(lambda x: ['background-color: #d62728' if v < 0 else 'background-color: #2ca02c' for v in x],
               subset=['PnL (Strategy %)']) \
        .highlight_max(subset=['PnL (Strategy %)', 'Win Rate (%)', 'Profit Factor', 'Sharpe Ratio'], color='#5fba7d') \
        .highlight_min(subset=['Max Drawdown (%)'], color='#d62728')
    return styler

def _render_mode1_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
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
                    interval=selected_interval, risk_manager=selected_rm)
                st.dataframe(metrics_df.style.format("{:.2f}"))
                st.plotly_chart(fig, use_container_width=True)

def _render_mode2_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    st.subheader("2. Анализ одной стратегии на разных инструментах (анализ робастности)")
    col1, col2, col3 = st.columns(3)
    with col1:
        selected_strategy = st.selectbox("Стратегия:", summary_df["Strategy"].unique(), key="c2_strat")
    with col2:
        selected_interval = st.selectbox("Интервал:", summary_df["Interval"].unique(), key="c2_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", summary_df["Risk Manager"].unique(), key="c2_rm")

    available_instruments = sorted(summary_df[
                                       (summary_df['Strategy'] == selected_strategy) &
                                       (summary_df['Interval'] == selected_interval) &
                                       (summary_df['Risk Manager'] == selected_rm)
                                       ]['Instrument'].unique())

    if not available_instruments:
        st.warning("Не найдено бэктестов для выбранной комбинации Стратегия/Интервал/РМ.")
        return

    select_all = st.checkbox("Выбрать все доступные инструменты", key="c2_select_all")

    if select_all:
        selected_instruments = st.multiselect(
            "Инструменты для агрегации:",
            options=available_instruments,
            default=available_instruments,
            key="c2_instrs_all"
        )
    else:
        selected_instruments = st.multiselect(
            "Инструменты для агрегации:",
            options=available_instruments,
            key="c2_instrs_manual"
        )

    if st.button("Анализировать стратегию", key="c2_btn"):
        if len(selected_instruments) < 2:
            st.warning("Пожалуйста, выберите хотя бы два инструмента.")
        else:
            with st.spinner("Выполняется анализ..."):
                metrics_df, fig = comp_analyzer.analyze_instrument_robustness(
                    strategy_name=selected_strategy, instruments=selected_instruments,
                    interval=selected_interval, risk_manager=selected_rm)
                st.dataframe(metrics_df.style.format(subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                                                     formatter="{:.2f}"))
                st.plotly_chart(fig, use_container_width=True)

def _render_mode3_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    st.subheader("3. Сравнение двух портфелей (A vs B)")
    col1, col2 = st.columns(2)
    with col1:
        params_a = _render_portfolio_selector_pane("Портфель A", "c3_A", summary_df)
    with col2:
        params_b = _render_portfolio_selector_pane("Портфель B", "c3_B", summary_df)
    st.markdown("---")
    if st.button("Сравнить портфели", key="c3_btn"):
        if not params_a or not params_b:
            st.error("Необходимо полностью сконфигурировать оба портфеля.")
            return
        with st.spinner("Выполняется сравнение портфелей..."):
            metrics_df, fig = comp_analyzer.compare_two_portfolios(
                portfolio_a_params=params_a,
                portfolio_b_params=params_b
            )
            st.dataframe(metrics_df.style.format(subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                                                 formatter="{:.2f}"))
            st.plotly_chart(fig, use_container_width=True)

def render_detailed_analysis_section(filtered_df: pd.DataFrame):
    st.header("Детальный анализ отдельного бэктеста")
    if filtered_df.empty:
        st.warning("По выбранным фильтрам не найдено ни одного бэктеста для детального анализа.")
        return
    selected_file = st.selectbox("Выберите бэктест для детального анализа:", options=filtered_df["File"].tolist())
    if selected_file:
        trades_df = load_trades_from_file(os.path.join(PATH_CONFIG["LOGS_DIR"], selected_file))

        row = filtered_df[filtered_df["File"] == selected_file].iloc[0]
        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], row["Exchange"], row["Interval"],
                                 f"{row['Instrument'].upper()}.parquet")
        historical_data = pd.read_parquet(data_path)

        analyzer = SingleRunAnalyzer(
            trades_df=trades_df, historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            interval=row["Interval"], risk_manager_type=row["Risk Manager"],
            exchange=row["Exchange"]
        )

        analyzer.calculator.trades['drawdown_percent'] = (analyzer.calculator.trades['equity_curve'] /
                                                          analyzer.calculator.trades['equity_curve'].cummax() - 1) * 100

        tab1, tab2, tab3 = st.tabs(["📈 Кривая капитала", "📊 Анализ PnL", "🕯️ График сделок"])
        with tab1: plot_equity_and_drawdown(analyzer)
        with tab2: plot_pnl_distribution(analyzer); plot_monthly_pnl(analyzer)
        with tab3: plot_trades_on_chart(historical_data, trades_df)

def render_comparative_analysis_section(summary_df: pd.DataFrame):
    st.divider()
    st.header("🔬 Сравнительный анализ")
    comp_analyzer = ComparativeAnalyzer(summary_df)
    comparison_mode = st.radio(
        "Выберите режим сравнения:",
        ["1. Стратегия vs Стратегия", "2. Анализ робастности", "3. Портфель vs Портфель"],
        horizontal=True)
    st.markdown("---")
    if "1." in comparison_mode:
        _render_mode1_ui(comp_analyzer, summary_df)
    elif "2." in comparison_mode:
        _render_mode2_ui(comp_analyzer, summary_df)
    elif "3." in comparison_mode:
        _render_mode3_ui(comp_analyzer, summary_df)

def main():
    st.title("🤖 Панель анализа торговых стратегий")

    summary_df, failed_files = load_all_backtests(PATH_CONFIG["LOGS_DIR"])

    if failed_files:
        with st.expander("⚠️ Обнаружены проблемы при загрузке некоторых бэктестов"):
            for error_msg in failed_files:
                st.warning(error_msg)

    if summary_df.empty:
        st.warning(
            "Не найдено ни одного корректно обработанного файла с результатами бэктестов (`_trades.jsonl`) в папке `logs/`.")
        st.info(
            "Убедитесь, что для каждого лога сделок существует соответствующий файл с историческими данными в папке `data/`.")
        return

    st.sidebar.header("Фильтры")
    selected_exchanges = st.sidebar.multiselect("Биржи", options=summary_df["Exchange"].unique(),
                                                default=summary_df["Exchange"].unique())
    selected_strategies = st.sidebar.multiselect("Стратегии", options=summary_df["Strategy"].unique(),
                                                 default=summary_df["Strategy"].unique())
    selected_instruments = st.sidebar.multiselect("Инструменты", options=summary_df["Instrument"].unique(),
                                                  default=summary_df["Instrument"].unique())
    selected_rms = st.sidebar.multiselect("Риск-менеджеры", options=summary_df["Risk Manager"].unique(),
                                          default=summary_df["Risk Manager"].unique())

    filtered_df = summary_df[
        (summary_df["Exchange"].isin(selected_exchanges)) &
        (summary_df["Strategy"].isin(selected_strategies)) &
        (summary_df["Instrument"].isin(selected_instruments)) &
        (summary_df["Risk Manager"].isin(selected_rms))
        ]

    st.header("Сводная таблица результатов")
    if not filtered_df.empty:
        df_display = filtered_df.copy()
        df_display.index = pd.RangeIndex(start=1, stop=len(df_display) + 1, step=1)
        st.dataframe(style_summary_table(df_display), use_container_width=True)
    else:
        st.dataframe(filtered_df, use_container_width=True)

    render_detailed_analysis_section(filtered_df)
    render_comparative_analysis_section(summary_df)

if __name__ == "__main__":
    main()