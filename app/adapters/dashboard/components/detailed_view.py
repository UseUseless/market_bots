"""
Компонент детального просмотра (Detailed View).

Этот модуль отвечает за визуализацию результатов конкретного бэктеста.
Он строит интерактивные графики (Equity Curve, Drawdown, PnL) используя библиотеку Plotly.
"""

import os
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np

from app.shared.time_helper import interval_to_timedelta
from app.infrastructure.storage.file_io import load_trades_from_file
from app.core.analysis.metrics import PortfolioMetricsCalculator, BenchmarkMetricsCalculator
from app.shared.primitives import TradeDirection
from app.shared.config import config

PATH_CONFIG = config.PATH_CONFIG
BACKTEST_CONFIG = config.BACKTEST_CONFIG
EXCHANGE_SPECIFIC_CONFIG = config.EXCHANGE_SPECIFIC_CONFIG


def plot_equity_and_drawdown(
        portfolio_equity: pd.Series,
        drawdown_percent: pd.Series,
        benchmark_equity: pd.Series
):
    """
    Строит комбинированный график: Кривая капитала + Просадка.

    Args:
        portfolio_equity (pd.Series): Временной ряд капитала стратегии.
        drawdown_percent (pd.Series): Временной ряд просадки в %.
        benchmark_equity (pd.Series): Временной ряд капитала Buy & Hold.
    """
    if portfolio_equity.empty:
        st.warning("Нет данных для построения графика капитала.")
        return

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.7, 0.3]
    )

    # 1. График капитала (Equity Curve)
    fig.add_trace(go.Scatter(
        x=portfolio_equity.index, y=portfolio_equity,
        mode='lines', name='Стратегия', line=dict(color='#2ca02c', width=2)
    ), row=1, col=1)

    # 2. График Benchmark (Buy & Hold)
    if not benchmark_equity.empty:
        # Ресемплинг индекса бенчмарка для соответствия точкам сделок стратегии
        # (для корректного визуального сравнения на оси X, которая основана на сделках)
        resampled_index = np.linspace(0, len(portfolio_equity) - 1, len(benchmark_equity))
        fig.add_trace(go.Scatter(
            x=resampled_index, y=benchmark_equity.values,
            mode='lines', name='Buy & Hold', line=dict(dash='dash', color='grey')
        ), row=1, col=1)

    # 3. График просадки (Drawdown)
    fig.add_trace(go.Scatter(
        x=drawdown_percent.index, y=drawdown_percent,
        mode='lines', name='Просадка', fill='tozeroy', line=dict(color='#d62728', width=1)
    ), row=2, col=1)

    fig.update_layout(
        title_text="Кривая капитала и просадки",
        height=600,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="Капитал ($/₽)", row=1, col=1)
    fig.update_yaxes(title_text="Просадка (%)", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)


def plot_pnl_distribution(trades_df: pd.DataFrame):
    """
    Строит гистограмму распределения прибыли/убытков по сделкам.
    Помогает оценить "толстые хвосты" и частоту выигрышей.
    """
    fig = px.histogram(
        trades_df, x="pnl", nbins=50,
        title="Распределение PnL",
        labels={"pnl": "Прибыль/Убыток"},
        color_discrete_sequence=['#1f77b4']
    )
    st.plotly_chart(fig, use_container_width=True)


def plot_monthly_pnl(trades_df: pd.DataFrame):
    """
    Строит столбчатую диаграмму доходности по месяцам.
    """
    df = trades_df.copy()
    df['exit_timestamp_utc'] = pd.to_datetime(df['exit_timestamp_utc'])
    df.set_index('exit_timestamp_utc', inplace=True)

    # Агрегация PnL по месяцам (ME = Month End)
    monthly_pnl = df['pnl'].resample('ME').sum().reset_index()
    monthly_pnl['month'] = monthly_pnl['exit_timestamp_utc'].dt.strftime('%Y-%m')

    fig = px.bar(
        monthly_pnl, x='month', y='pnl',
        title="PnL по месяцам",
        labels={"pnl": "Суммарный PnL", "month": "Месяц"},
        color='pnl',
        color_continuous_scale=px.colors.diverging.RdYlGn
    )
    st.plotly_chart(fig, use_container_width=True)


def plot_trades_on_chart(historical_data: pd.DataFrame, trades_df: pd.DataFrame, interval_str: str):
    """
    Визуализирует точки входа и выхода на графике цены (Candlestick).

    Args:
        historical_data: DataFrame со свечами (OHLCV).
        trades_df: DataFrame со сделками.
        interval_str: Интервал свечей (для визуальной коррекции меток).
    """
    fig = go.Figure(data=go.Candlestick(
        x=historical_data['time'],
        open=historical_data['open'], high=historical_data['high'],
        low=historical_data['low'], close=historical_data['close'],
        name='Цена'
    ))

    # Коррекция времени сделок
    # Сделки совершаются по ценам Close/Open свечи, но для красивого отображения
    # маркеры лучше сдвигать к моменту Open соответствующей свечи.
    delta = interval_to_timedelta(interval_str)

    trades_df['entry_timestamp_utc'] = pd.to_datetime(trades_df['entry_timestamp_utc'])
    trades_df['exit_timestamp_utc'] = pd.to_datetime(trades_df['exit_timestamp_utc'])

    # Визуальный сдвиг назад
    trades_df['plot_entry_time'] = trades_df['entry_timestamp_utc'] - delta
    trades_df['plot_exit_time'] = trades_df['exit_timestamp_utc'] - delta

    # 1. Маркеры входа (Треугольники)
    long_entries = trades_df[trades_df['direction'] == TradeDirection.BUY]
    short_entries = trades_df[trades_df['direction'] == TradeDirection.SELL]

    fig.add_trace(go.Scatter(
        x=long_entries['plot_entry_time'], y=long_entries['entry_price'],
        mode='markers', marker=dict(symbol='triangle-up', color='green', size=12),
        name='Вход Long'
    ))
    fig.add_trace(go.Scatter(
        x=short_entries['plot_entry_time'], y=short_entries['entry_price'],
        mode='markers', marker=dict(symbol='triangle-down', color='red', size=12),
        name='Вход Short'
    ))

    # 2. Маркеры выхода (Кружки/Крестики)
    # Группируем по причине выхода для разного стиля
    exit_styles = {
        'Take Profit': {'symbol': 'circle', 'color': '#2ca02c'},
        'Stop Loss': {'symbol': 'circle-x', 'color': '#d62728'},
        'Signal': {'symbol': 'x', 'color': 'orange'}
    }

    for reason, style in exit_styles.items():
        exits = trades_df[trades_df['exit_reason'] == reason]
        if not exits.empty:
            fig.add_trace(go.Scatter(
                x=exits['plot_exit_time'], y=exits['exit_price'],
                mode='markers',
                marker=dict(symbol=style['symbol'], color=style['color'], size=10, line=dict(width=1, color='black')),
                name=f'Выход ({reason})'
            ))

    fig.update_layout(
        title_text="Анализ сделок на графике",
        xaxis_title="Время",
        yaxis_title="Цена",
        xaxis_rangeslider_visible=False,
        height=700
    )
    st.plotly_chart(fig, use_container_width=True)


def render_detailed_view(filtered_df: pd.DataFrame):
    """
    Главная функция отрисовки страницы детального анализа.

    Args:
        filtered_df (pd.DataFrame): Список бэктестов после фильтрации в сайдбаре.
    """
    st.header("Детальный анализ стратегии")

    if filtered_df.empty:
        st.info("Выберите фильтры в боковой панели, чтобы увидеть результаты.")
        return

    # Селектор конкретного файла из отфильтрованного списка
    selected_file_name = st.selectbox(
        "Выберите файл для анализа:",
        options=filtered_df["File"].tolist()
    )

    if selected_file_name:
        # 1. Получение данных выбранной строки
        row = filtered_df[filtered_df["File"] == selected_file_name].iloc[0]

        # 2. Загрузка сырых данных (сделки + свечи)
        trades_df = load_trades_from_file(row["File Path"])

        data_path = os.path.join(
            PATH_CONFIG["DATA_DIR"], row["Exchange"], row["Interval"],
            f"{row['Instrument'].upper()}.parquet"
        )

        # Обработка случая, когда данных нет (например, удалили папку data)
        try:
            historical_data = pd.read_parquet(data_path)
        except FileNotFoundError:
            st.error(f"Файл с историей не найден: {data_path}")
            return

        # 3. Пересчет метрик (для отрисовки графиков, а не просто чисел)
        # Мы используем те же калькуляторы, что и в Core, гарантируя консистентность.
        annual_factor = EXCHANGE_SPECIFIC_CONFIG.get(row["Exchange"], {}).get("SHARPE_ANNUALIZATION_FACTOR", 252)

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

        # 4. Подготовка данных для графиков
        # equity_curve уже содержит ряд капитала по времени выхода из сделок
        portfolio_equity = portfolio_calc.trades['equity_curve']

        # Расчет просадки в процентах для графика
        peak_equity = portfolio_equity.cummax()
        drawdown_percent = (portfolio_equity - peak_equity) / peak_equity * 100

        benchmark_equity = benchmark_calc.equity_curve if benchmark_calc.is_valid else pd.Series()

        # 5. Визуализация в табах
        tab1, tab2, tab3 = st.tabs(["📈 Капитал & Просадка", "📊 Статистика PnL", "🕯️ Точки входа"])

        with tab1:
            plot_equity_and_drawdown(portfolio_equity, drawdown_percent, benchmark_equity)

        with tab2:
            col1, col2 = st.columns(2)
            with col1:
                plot_pnl_distribution(trades_df)
            with col2:
                plot_monthly_pnl(trades_df)

        with tab3:
            plot_trades_on_chart(historical_data, trades_df, row["Interval"])