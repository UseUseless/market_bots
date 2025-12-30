"""
Боковая панель.

Орисовка элементов управления в левой панели дашборда Streamlit.
Позволяет фильтровать массив результатов бэктестов
по ключевым измерениям: Биржа, Стратегия, Инструмент, Риск-менеджмент.
"""

import streamlit as st
import pandas as pd


def render_sidebar(summary_df: pd.DataFrame) -> pd.DataFrame:
    """
    Отрисовывает фильтры в сайдбаре и применяет их к данным.

    Создает виджеты `multiselect` для каждого ключевого поля.
    По умолчанию выбираются все доступные опции.

    Args:
        summary_df (pd.DataFrame): Полный DataFrame со сводными результатами
                                   всех загруженных бэктестов.

    Returns:
        pd.DataFrame: Новый DataFrame, содержащий только строки, соответствующие
                      выбранным критериям фильтрации.
    """
    st.sidebar.header("Фильтры")

    # 1. Фильтр по Биржам
    # Сортировка опций нужна для детерминированного порядка в UI
    exchange_options = sorted(summary_df["Exchange"].unique())
    selected_exchanges = st.sidebar.multiselect(
        "Биржи",
        options=exchange_options,
        default=exchange_options  # По умолчанию выбрано всё
    )

    # 2. Фильтр по Стратегиям
    strategy_options = sorted(summary_df["Strategy"].unique())
    selected_strategies = st.sidebar.multiselect(
        "Стратегии",
        options=strategy_options,
        default=strategy_options
    )

    # 3. Фильтр по Инструментам
    instrument_options = sorted(summary_df["Instrument"].unique())
    selected_instruments = st.sidebar.multiselect(
        "Инструменты",
        options=instrument_options,
        default=instrument_options
    )

    # 4. Фильтр по Риск-менеджерам
    rm_options = sorted(summary_df["Risk Manager"].unique())
    selected_rms = st.sidebar.multiselect(
        "Риск-менеджеры",
        options=rm_options,
        default=rm_options
    )

    # Применение фильтров
    # Логическое И (&) между условиями: строка должна удовлетворять всем фильтрам
    filtered_df = summary_df[
        (summary_df["Exchange"].isin(selected_exchanges)) &
        (summary_df["Strategy"].isin(selected_strategies)) &
        (summary_df["Instrument"].isin(selected_instruments)) &
        (summary_df["Risk Manager"].isin(selected_rms))
    ].copy()

    return filtered_df