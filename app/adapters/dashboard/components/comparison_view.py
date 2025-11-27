import streamlit as st
import pandas as pd
from typing import Dict, Any, Optional

from app.core.analysis.comparative import ComparativeAnalyzer

def _render_portfolio_selector_pane(pane_title: str, key_prefix: str, summary_df: pd.DataFrame) -> Optional[
    Dict[str, Any]]:
    """
    Отрисовывает одну колонку для выбора параметров портфеля.
    Эта функция-хелпер используется в режиме "Портфель vs Портфель".
    """
    st.subheader(pane_title)

    # Уникальные значения для селекторов, отсортированные для удобства
    unique_strategies = sorted(summary_df["Strategy"].unique())
    unique_intervals = sorted(summary_df["Interval"].unique())
    unique_rms = sorted(summary_df["Risk Manager"].unique())

    selected_strategy = st.selectbox("Стратегия:", unique_strategies, key=f"{key_prefix}_strat")
    selected_interval = st.selectbox("Интервал:", unique_intervals, key=f"{key_prefix}_interval")
    selected_rm = st.selectbox("Риск-менеджер:", unique_rms, key=f"{key_prefix}_rm")

    # Фильтруем доступные инструменты на основе сделанных выборов
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


def _render_mode1_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """Отрисовывает UI для режима 1: Сравнение стратегий на одном инструменте."""
    st.subheader("1. Сравнение стратегий на одном инструменте")

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_instrument = st.selectbox("Инструмент:", sorted(summary_df["Instrument"].unique()), key="c1_instr")
    with col2:
        selected_interval = st.selectbox("Интервал:", sorted(summary_df["Interval"].unique()), key="c1_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", sorted(summary_df["Risk Manager"].unique()), key="c1_rm")

    selected_strategies = st.multiselect(
        "Выберите стратегии для сравнения:",
        sorted(summary_df["Strategy"].unique()),
        key="c1_strats"
    )

    if st.button("Сравнить стратегии", key="c1_btn"):
        if len(selected_strategies) < 2:
            st.warning("Пожалуйста, выберите хотя бы две стратегии.")
        else:
            with st.spinner("Выполняется сравнение..."):
                metrics_df, fig = comp_analyzer.compare_strategies_on_instrument(
                    strategy_names=selected_strategies,
                    instrument=selected_instrument,
                    interval=selected_interval,
                    risk_manager=selected_rm
                )
                if metrics_df.empty:
                    st.error("Не найдено данных для сравнения по заданным параметрам.")
                else:
                    st.dataframe(metrics_df.style.format("{:.2f}"))
                    st.plotly_chart(fig, use_container_width=True)


def _render_mode2_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """Отрисовывает UI для режима 2: Анализ робастности одной стратегии."""
    st.subheader("2. Анализ одной стратегии на разных инструментах (анализ робастности)")

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
        st.warning("Не найдено бэктестов для выбранной комбинации Стратегия/Интервал/РМ.")
        return

    select_all = st.checkbox("Выбрать все доступные инструменты", key="c2_select_all", value=True)

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
                    strategy_name=selected_strategy,
                    instruments=selected_instruments,
                    interval=selected_interval,
                    risk_manager=selected_rm
                )
                if metrics_df.empty:
                    st.error("Не найдено данных для анализа по заданным параметрам.")
                else:
                    st.dataframe(metrics_df.style.format(
                        subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                        formatter="{:.2f}"
                    ))
                    st.plotly_chart(fig, use_container_width=True)


def _render_mode3_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """Отрисовывает UI для режима 3: Сравнение двух портфелей."""
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
            metrics_df, equity_curves = comp_analyzer.compare_two_portfolios(
                portfolio_a_params=params_a,
                portfolio_b_params=params_b
            )
            if metrics_df.empty:
                st.error("Не удалось собрать данные для сравнения портфелей.")
            else:
                st.dataframe(metrics_df.style.format(
                    subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                    formatter="{:.2f}"
                ))
                import plotly.graph_objects as go
                fig = go.Figure()
                for name, curve in equity_curves.items():
                    fig.add_trace(go.Scatter(x=curve.index, y=curve.values, mode='lines', name=name))
                fig.update_layout(title_text="Сравнение кривых капитала портфелей")
                st.plotly_chart(fig, use_container_width=True)

def render_comparison_view(summary_df: pd.DataFrame):
    """
    Отрисовывает всю секцию сравнительного анализа в дашборде.

    :param summary_df: Полный, нефильтрованный DataFrame со сводкой по всем бэктестам.
    """
    st.divider()
    st.header("🔬 Сравнительный анализ")

    # Инициализируем анализатор, который будет выполнять всю тяжелую работу
    try:
        comp_analyzer = ComparativeAnalyzer(summary_df)
    except ValueError as e:
        st.error(f"Ошибка инициализации анализатора: {e}")
        return

    # Радио-кнопки для выбора режима
    comparison_mode = st.radio(
        "Выберите режим сравнения:",
        ["1. Стратегия vs Стратегия", "2. Анализ робастности", "3. Портфель vs Портфель"],
        horizontal=True,
        label_visibility="collapsed"  # Скрываем заголовок, т.к. он уже есть в st.header
    )

    st.markdown("---")

    # В зависимости от выбора, вызываем соответствующую функцию отрисовки
    if "1." in comparison_mode:
        _render_mode1_ui(comp_analyzer, summary_df)
    elif "2." in comparison_mode:
        _render_mode2_ui(comp_analyzer, summary_df)
    elif "3." in comparison_mode:
        _render_mode3_ui(comp_analyzer, summary_df)