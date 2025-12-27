"""
Компонент сравнительного анализа (Comparison View).

Этот модуль отвечает за UI-часть сравнения результатов бэктестов в Streamlit.
Он позволяет пользователю выбирать стратегии, инструменты и режимы сравнения,
а затем отображает результаты, рассчитанные в `ComparativeAnalyzer`.

Поддерживаемые режимы:
1. Сравнение стратегий: Как разные алгоритмы отработали на одном активе.
2. Анализ робастности: Как одна стратегия работает на портфеле активов.
3. Сравнение портфелей: A/B тестирование групп активов.
"""

from typing import Dict, Any, Optional

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from app.core.analysis.comparative import ComparativeAnalyzer


def _render_portfolio_selector_pane(
        pane_title: str,
        key_prefix: str,
        summary_df: pd.DataFrame
) -> Optional[Dict[str, Any]]:
    """
    Рендерит панель выбора параметров для формирования портфеля.
    """
    st.subheader(pane_title)

    unique_strategies = sorted(summary_df["Strategy"].unique())
    unique_intervals = sorted(summary_df["Interval"].unique())
    unique_rms = sorted(summary_df["Risk Manager"].unique())

    selected_strategy = st.selectbox("Стратегия:", unique_strategies, key=f"{key_prefix}_strat")
    selected_interval = st.selectbox("Интервал:", unique_intervals, key=f"{key_prefix}_interval")
    selected_rm = st.selectbox("Риск-менеджер:", unique_rms, key=f"{key_prefix}_rm")

    available_instruments = sorted(summary_df[
                                       (summary_df['Strategy'] == selected_strategy) &
                                       (summary_df['Interval'] == selected_interval) &
                                       (summary_df['Risk Manager'] == selected_rm)
                                       ]['Instrument'].unique())

    if not available_instruments:
        st.warning("Нет данных для этой комбинации параметров.")
        return None

    select_all = st.checkbox("Выбрать все доступные", key=f"{key_prefix}_select_all")

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
        st.info("Выберите хотя бы один инструмент.")
        return None

    return {
        "strategy": selected_strategy,
        "interval": selected_interval,
        "rm": selected_rm,
        "instruments": selected_instruments
    }


def _render_mode1_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """
    UI Режим 1: Сравнение разных стратегий на одном инструменте.
    """
    st.subheader("1. Сравнение стратегий на одном инструменте")

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_instrument = st.selectbox("Инструмент:", sorted(summary_df["Instrument"].unique()), key="c1_instr")
    with col2:
        selected_interval = st.selectbox("Интервал:", sorted(summary_df["Interval"].unique()), key="c1_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", sorted(summary_df["Risk Manager"].unique()), key="c1_rm")

    available_strategies = sorted(summary_df["Strategy"].unique())
    selected_strategies = st.multiselect(
        "Выберите стратегии для сравнения:",
        available_strategies,
        key="c1_strats"
    )

    if st.button("Сравнить стратегии", key="c1_btn"):
        if len(selected_strategies) < 2:
            st.warning("Выберите хотя бы две стратегии для сравнения.")
        else:
            with st.spinner("Выполняется сравнение..."):
                # Analyzer возвращает (DataFrame, Dict[str, Series])
                metrics_df, equity_curves = comp_analyzer.compare_strategies_on_instrument(
                    strategy_names=selected_strategies,
                    instrument=selected_instrument,
                    interval=selected_interval,
                    risk_manager=selected_rm
                )

                if metrics_df.empty:
                    st.error("Не найдено данных для сравнения по заданным параметрам.")
                else:
                    st.dataframe(metrics_df.style.format("{:.2f}"))

                    # --- Построение графика ---
                    fig = go.Figure()
                    for strat_name, curve in equity_curves.items():
                        fig.add_trace(go.Scatter(
                            x=curve.index,  # DatetimeIndex или RangeIndex
                            y=curve.values,
                            mode='lines',
                            name=strat_name
                        ))

                    fig.update_layout(
                        title="Сравнение доходности стратегий",
                        xaxis_title="Дата/Сделки",
                        yaxis_title="Капитал",
                        height=500
                    )
                    st.plotly_chart(fig, use_container_width=True)


def _render_mode2_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """
    UI Режим 2: Анализ робастности стратегии.
    """
    st.subheader("2. Анализ стратегии на портфеле инструментов")

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_strategy = st.selectbox("Стратегия:", sorted(summary_df["Strategy"].unique()), key="c2_strat")
    with col2:
        selected_interval = st.selectbox("Интервал:", sorted(summary_df["Interval"].unique()), key="c2_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", sorted(summary_df["Risk Manager"].unique()), key="c2_rm")

    available_instruments = sorted(summary_df[
                                       (summary_df['Strategy'] == selected_strategy) &
                                       (summary_df['Interval'] == selected_interval) &
                                       (summary_df['Risk Manager'] == selected_rm)
                                       ]['Instrument'].unique())

    if not available_instruments:
        st.warning("Нет бэктестов для этой комбинации.")
        return

    select_all = st.checkbox("Выбрать все доступные", key="c2_select_all", value=True)

    if select_all:
        selected_instruments = st.multiselect(
            "Инструменты для портфеля:",
            options=available_instruments,
            default=available_instruments,
            key="c2_instrs_all"
        )
    else:
        selected_instruments = st.multiselect(
            "Инструменты для портфеля:",
            options=available_instruments,
            key="c2_instrs_manual"
        )

    if st.button("Анализировать портфель", key="c2_btn"):
        if len(selected_instruments) < 2:
            st.warning("Выберите хотя бы два инструмента для агрегации.")
        else:
            with st.spinner("Агрегация результатов..."):
                # Analyzer возвращает (DataFrame, Series)
                metrics_df, portfolio_curve = comp_analyzer.analyze_instrument_robustness(
                    strategy_name=selected_strategy,
                    instruments=selected_instruments,
                    interval=selected_interval,
                    risk_manager=selected_rm
                )

                if metrics_df.empty:
                    st.error("Ошибка при расчете метрик.")
                else:
                    st.dataframe(metrics_df.style.format(
                        subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                        formatter="{:.2f}"
                    ))

                    # --- Построение графика ---
                    if portfolio_curve is not None and not portfolio_curve.empty:
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=portfolio_curve.index,
                            y=portfolio_curve.values,
                            mode='lines',
                            name='Портфель (Equity)',
                            line=dict(color='#2ca02c', width=2)
                        ))

                        fig.update_layout(
                            title=f"Кривая капитала портфеля ({len(selected_instruments)} инструментов)",
                            xaxis_title="Время",
                            yaxis_title="Капитал",
                            height=500
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.warning("Не удалось построить график капитала (нет данных).")


def _render_mode3_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """
    UI Режим 3: Сравнение двух произвольных портфелей (A/B тест).
    """
    st.subheader("3. Сравнение двух портфелей (A vs B)")

    col1, col2 = st.columns(2)
    with col1:
        params_a = _render_portfolio_selector_pane("Портфель A", "c3_A", summary_df)
    with col2:
        params_b = _render_portfolio_selector_pane("Портфель B", "c3_B", summary_df)

    st.markdown("---")

    if st.button("Сравнить портфели", key="c3_btn"):
        if not params_a or not params_b:
            st.error("Необходимо полностью настроить оба портфеля.")
            return

        with st.spinner("Сравнение портфелей..."):
            metrics_df, equity_curves = comp_analyzer.compare_two_portfolios(
                portfolio_a_params=params_a,
                portfolio_b_params=params_b
            )

            if metrics_df.empty:
                st.error("Не удалось рассчитать метрики.")
            else:
                st.dataframe(metrics_df.style.format(
                    subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                    formatter="{:.2f}"
                ))

                # --- Построение графика ---
                fig = go.Figure()
                for name, curve in equity_curves.items():
                    fig.add_trace(go.Scatter(
                        x=curve.index, y=curve.values,
                        mode='lines', name=name
                    ))

                fig.update_layout(
                    title_text="Сравнение доходности портфелей",
                    yaxis_title="Капитал",
                    xaxis_title="Дата"
                )
                st.plotly_chart(fig, use_container_width=True)


def render_comparison_view(summary_df: pd.DataFrame):
    """
    Главная функция отрисовки страницы сравнительного анализа.
    """
    st.divider()
    st.header("🔬 Сравнительный анализ")

    try:
        comp_analyzer = ComparativeAnalyzer(summary_df)
    except ValueError as e:
        st.error(f"Ошибка инициализации анализатора: {e}")
        return

    comparison_mode = st.radio(
        "Выберите режим сравнения:",
        ["1. Стратегия vs Стратегия", "2. Анализ робастности", "3. Портфель vs Портфель"],
        horizontal=True,
        label_visibility="collapsed"
    )

    st.markdown("---")

    if "1." in comparison_mode:
        _render_mode1_ui(comp_analyzer, summary_df)
    elif "2." in comparison_mode:
        _render_mode2_ui(comp_analyzer, summary_df)
    elif "3." in comparison_mode:
        _render_mode3_ui(comp_analyzer, summary_df)