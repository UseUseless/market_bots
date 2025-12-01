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
    Используется в режиме "Портфель vs Портфель" для создания двух независимых наборов.

    Args:
        pane_title (str): Заголовок панели (например, "Портфель А").
        key_prefix (str): Уникальный префикс для ключей виджетов Streamlit.
        summary_df (pd.DataFrame): Сводная таблица всех бэктестов.

    Returns:
        Optional[Dict]: Словарь с выбранными параметрами или None, если выбор не завершен.
    """
    st.subheader(pane_title)

    # Получаем уникальные значения для фильтров
    unique_strategies = sorted(summary_df["Strategy"].unique())
    unique_intervals = sorted(summary_df["Interval"].unique())
    unique_rms = sorted(summary_df["Risk Manager"].unique())

    # Селекторы параметров
    selected_strategy = st.selectbox("Стратегия:", unique_strategies, key=f"{key_prefix}_strat")
    selected_interval = st.selectbox("Интервал:", unique_intervals, key=f"{key_prefix}_interval")
    selected_rm = st.selectbox("Риск-менеджер:", unique_rms, key=f"{key_prefix}_rm")

    # Фильтруем инструменты, доступные для выбранной комбинации параметров
    available_instruments = sorted(summary_df[
        (summary_df['Strategy'] == selected_strategy) &
        (summary_df['Interval'] == selected_interval) &
        (summary_df['Risk Manager'] == selected_rm)
    ]['Instrument'].unique())

    if not available_instruments:
        st.warning("Нет данных для этой комбинации параметров.")
        return None

    # Мульти-селект инструментов
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
    Позволяет понять, какой алгоритм лучше подходит для конкретного актива.
    """
    st.subheader("1. Сравнение стратегий на одном инструменте")

    # Выбор общих параметров
    col1, col2, col3 = st.columns(3)
    with col1:
        selected_instrument = st.selectbox("Инструмент:", sorted(summary_df["Instrument"].unique()), key="c1_instr")
    with col2:
        selected_interval = st.selectbox("Интервал:", sorted(summary_df["Interval"].unique()), key="c1_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", sorted(summary_df["Risk Manager"].unique()), key="c1_rm")

    # Выбор сравниваемых стратегий
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
                metrics_df, fig = comp_analyzer.compare_strategies_on_instrument(
                    strategy_names=selected_strategies,
                    instrument=selected_instrument,
                    interval=selected_interval,
                    risk_manager=selected_rm
                )

                if metrics_df.empty:
                    st.error("Не найдено данных для сравнения по заданным параметрам.")
                else:
                    # Отображение таблицы метрик и графика Equity
                    st.dataframe(metrics_df.style.format("{:.2f}"))
                    st.plotly_chart(fig, use_container_width=True)


def _render_mode2_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """
    UI Режим 2: Анализ робастности стратегии.
    Показывает, как одна стратегия работает на корзине инструментов (агрегированный результат).
    """
    st.subheader("2. Анализ стратегии на портфеле инструментов")

    col1, col2, col3 = st.columns(3)
    with col1:
        selected_strategy = st.selectbox("Стратегия:", sorted(summary_df["Strategy"].unique()), key="c2_strat")
    with col2:
        selected_interval = st.selectbox("Интервал:", sorted(summary_df["Interval"].unique()), key="c2_interval")
    with col3:
        selected_rm = st.selectbox("Риск-менеджер:", sorted(summary_df["Risk Manager"].unique()), key="c2_rm")

    # Фильтр доступных инструментов
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
                metrics_df, fig = comp_analyzer.analyze_instrument_robustness(
                    strategy_name=selected_strategy,
                    instruments=selected_instruments,
                    interval=selected_interval,
                    risk_manager=selected_rm
                )

                if metrics_df.empty:
                    st.error("Ошибка при расчете метрик.")
                else:
                    # Форматирование: все float до 2 знаков, кроме int колонок
                    st.dataframe(metrics_df.style.format(
                        subset=pd.IndexSlice[:, metrics_df.columns != 'Total Trades'],
                        formatter="{:.2f}"
                    ))
                    st.plotly_chart(fig, use_container_width=True)


def _render_mode3_ui(comp_analyzer: ComparativeAnalyzer, summary_df: pd.DataFrame):
    """
    UI Режим 3: Сравнение двух произвольных портфелей (A/B тест).
    Позволяет сравнить, например, TrendFollowing на крипте vs MeanReversion на акциях.
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

                # Построение графика сравнения кривых капитала
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

    Инициализирует анализатор и переключает режимы отображения.

    Args:
        summary_df (pd.DataFrame): DataFrame со всеми результатами бэктестов.
    """
    st.divider()
    st.header("🔬 Сравнительный анализ")

    # Инициализация движка сравнения
    try:
        comp_analyzer = ComparativeAnalyzer(summary_df)
    except ValueError as e:
        st.error(f"Ошибка инициализации анализатора: {e}")
        return

    # Переключатель режимов
    comparison_mode = st.radio(
        "Выберите режим сравнения:",
        ["1. Стратегия vs Стратегия", "2. Анализ робастности", "3. Портфель vs Портфель"],
        horizontal=True,
        label_visibility="collapsed"
    )

    st.markdown("---")

    # Роутинг на конкретный рендер
    if "1." in comparison_mode:
        _render_mode1_ui(comp_analyzer, summary_df)
    elif "2." in comparison_mode:
        _render_mode2_ui(comp_analyzer, summary_df)
    elif "3." in comparison_mode:
        _render_mode3_ui(comp_analyzer, summary_df)