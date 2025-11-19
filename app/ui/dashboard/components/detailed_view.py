"""
Этот модуль отвечает за отрисовку секции "Детальный анализ" в дашборде Streamlit.

Он включает в себя:
- Выбор конкретного бэктеста из отфильтрованного списка.
- Загрузку необходимых данных (сделки, история).
- Использование калькуляторов метрик для получения числовых данных.
- Генерацию интерактивных графиков на нескольких вкладках.
"""

import os
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np

from app.utils.file_io import load_trades_from_file
from app.analyzers.metrics.portfolio_metrics import PortfolioMetricsCalculator
from app.analyzers.metrics.benchmark_metrics import BenchmarkMetricsCalculator
from config import PATH_CONFIG, BACKTEST_CONFIG, EXCHANGE_SPECIFIC_CONFIG


def plot_equity_and_drawdown(
        portfolio_equity: pd.Series,
        drawdown_percent: pd.Series,
        benchmark_equity: pd.Series
):
    """Строит график кривой капитала и просадок."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[0.7, 0.3])

    # График капитала стратегии
    fig.add_trace(go.Scatter(
        x=portfolio_equity.index, y=portfolio_equity,
        mode='lines', name='Кривая капитала'
    ), row=1, col=1)

    # График Buy & Hold
    if not benchmark_equity.empty:
        # Выравниваем индекс бенчмарка по количеству сделок для визуального сопоставления
        resampled_index = np.linspace(0, len(portfolio_equity) - 1, len(benchmark_equity))
        fig.add_trace(go.Scatter(
            x=resampled_index, y=benchmark_equity.values,
            mode='lines', name='Buy & Hold', line=dict(dash='dash', color='grey')
        ), row=1, col=1)

    # График просадки
    fig.add_trace(go.Scatter(
        x=drawdown_percent.index, y=drawdown_percent,
        mode='lines', name='Просадка', fill='tozeroy', line_color='red'
    ), row=2, col=1)

    fig.update_layout(title_text="Кривая капитала и просадки", height=600, legend_orientation="h", legend_y=1.15)
    fig.update_yaxes(title_text="Капитал", row=1, col=1)
    fig.update_yaxes(title_text="Просадка (%)", row=2, col=1)
    fig.update_xaxes(title_text="Количество сделок", row=2, col=1)
    st.plotly_chart(fig, use_container_width=True)


def plot_pnl_distribution(trades_df: pd.DataFrame):
    """Строит гистограмму распределения PnL по сделкам."""
    fig = px.histogram(trades_df, x="pnl", nbins=50,
                       title="Распределение PnL по сделкам",
                       labels={"pnl": "Прибыль/убыток по сделке"})
    st.plotly_chart(fig, use_container_width=True)


def plot_monthly_pnl(trades_df: pd.DataFrame):
    """Строит столбчатую диаграмму PnL по месяцам."""
    df = trades_df.copy()
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
    """Отображает сделки на свечном графике."""
    fig = go.Figure(data=go.Candlestick(
        x=historical_data['time'], open=historical_data['open'], high=historical_data['high'],
        low=historical_data['low'], close=historical_data['close'], name='Свечи'
    ))

    trades_df['entry_timestamp_utc'] = pd.to_datetime(trades_df['entry_timestamp_utc'])
    trades_df['exit_timestamp_utc'] = pd.to_datetime(trades_df['exit_timestamp_utc'])

    # Маркеры входа
    long_entries = trades_df[trades_df['direction'] == 'BUY']
    short_entries = trades_df[trades_df['direction'] == 'SELL']
    fig.add_trace(go.Scatter(
        x=long_entries['entry_timestamp_utc'], y=long_entries['entry_price'], mode='markers',
        marker=dict(symbol='triangle-up', color='green', size=12), name='Вход в Лонг'
    ))
    fig.add_trace(go.Scatter(
        x=short_entries['entry_timestamp_utc'], y=short_entries['entry_price'], mode='markers',
        marker=dict(symbol='triangle-down', color='red', size=12), name='Вход в Шорт'
    ))

    # Маркеры выхода
    for reason, symbol, color in [
        ('Take Profit', 'circle', '#2ca02c'),
        ('Stop Loss', 'circle', '#d62728'),
        ('Signal', 'x', 'orange')
    ]:
        exits = trades_df[trades_df['exit_reason'] == reason]
        fig.add_trace(go.Scatter(
            x=exits['exit_timestamp_utc'], y=exits['exit_price'], mode='markers',
            marker=dict(symbol=symbol, color=color, size=10, line=dict(width=2, color='DarkSlateGrey')),
            name=f'Выход ({reason})'
        ))

    fig.update_layout(
        title_text="График сделок на свечах", xaxis_title="Время", yaxis_title="Цена",
        xaxis_rangeslider_visible=False, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)

def render_detailed_view(filtered_df: pd.DataFrame):
    """
    Отрисовывает всю секцию детального анализа одного бэктеста.
    """
    st.header("Детальный анализ отдельного бэктеста")

    if filtered_df.empty:
        st.warning("По выбранным фильтрам не найдено ни одного бэктеста для детального анализа.")
        return

    # Выпадающий список для выбора конкретного файла
    selected_file = st.selectbox(
        "Выберите бэктест для детального анализа:",
        options=filtered_df["File"].tolist()
    )

    if selected_file:
        # --- 1. Загрузка данных ---
        row = filtered_df[filtered_df["File"] == selected_file].iloc[0]
        full_log_path = row["File Path"]
        trades_df = load_trades_from_file(full_log_path)
        data_path = os.path.join(
            PATH_CONFIG["DATA_DIR"], row["Exchange"], row["Interval"],
            f"{row['Instrument'].upper()}.parquet"
        )
        historical_data = pd.read_parquet(data_path)

        # --- 2. Расчет метрик с помощью новых калькуляторов ---
        annual_factor = EXCHANGE_SPECIFIC_CONFIG[row["Exchange"]]["SHARPE_ANNUALIZATION_FACTOR"]

        portfolio_calc = PortfolioMetricsCalculator(
            trades_df=trades_df,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            annualization_factor=annual_factor
        )

        benchmark_calc = BenchmarkMetricsCalculator(
            historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            annualization_factor=annual_factor
        )

        # Получаем данные для графиков
        portfolio_equity = portfolio_calc.trades['equity_curve']
        drawdown_percent = (portfolio_equity / portfolio_equity.cummax() - 1) * 100
        benchmark_equity = benchmark_calc.equity_curve if benchmark_calc.is_valid else pd.Series()

        # --- 3. Отрисовка вкладок и графиков ---
        tab1, tab2, tab3 = st.tabs(["📈 Кривая капитала", "📊 Анализ PnL", "🕯️ График сделок"])

        with tab1:
            plot_equity_and_drawdown(portfolio_equity, drawdown_percent, benchmark_equity)

        with tab2:
            plot_pnl_distribution(trades_df)
            plot_monthly_pnl(trades_df)

        with tab3:
            plot_trades_on_chart(historical_data, trades_df)