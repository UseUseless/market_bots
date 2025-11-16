"""
Модуль для отрисовки боковой панели (sidebar) и фильтрации данных в дашборде.

Этот компонент отвечает за:
1. Отображение всех доступных фильтров (Биржа, Стратегия, Инструмент, Риск-менеджер).
2. Получение выбора пользователя из этих фильтров.
3. Применение фильтров к исходному DataFrame.
4. Возврат отфильтрованного DataFrame для дальнейшего отображения.
"""

import streamlit as st
import pandas as pd

def render_sidebar(summary_df: pd.DataFrame) -> pd.DataFrame:
    """
    Отрисовывает боковую панель с фильтрами и возвращает отфильтрованный DataFrame.

    Args:
        summary_df (pd.DataFrame): Полный, нефильтрованный DataFrame со сводными
                                   результатами всех бэктестов.

    Returns:
        pd.DataFrame: Отфильтрованный DataFrame на основе выбора пользователя в сайдбаре.
    """
    st.sidebar.header("Фильтры")

    # --- Фильтр по Биржам ---
    # Получаем уникальные значения, сортируем для предсказуемого порядка
    exchange_options = sorted(summary_df["Exchange"].unique())
    selected_exchanges = st.sidebar.multiselect(
        "Биржи",
        options=exchange_options,
        default=exchange_options  # По умолчанию выбраны все
    )

    # --- Фильтр по Стратегиям ---
    strategy_options = sorted(summary_df["Strategy"].unique())
    selected_strategies = st.sidebar.multiselect(
        "Стратегии",
        options=strategy_options,
        default=strategy_options
    )

    # --- Фильтр по Инструментам ---
    instrument_options = sorted(summary_df["Instrument"].unique())
    selected_instruments = st.sidebar.multiselect(
        "Инструменты",
        options=instrument_options,
        default=instrument_options
    )

    # --- Фильтр по Риск-менеджерам ---
    rm_options = sorted(summary_df["Risk Manager"].unique())
    selected_rms = st.sidebar.multiselect(
        "Риск-менеджеры",
        options=rm_options,
        default=rm_options
    )

    # Используем .copy(), чтобы избежать SettingWithCopyWarning от Pandas в будущем
    filtered_df = summary_df[
        (summary_df["Exchange"].isin(selected_exchanges)) &
        (summary_df["Strategy"].isin(selected_strategies)) &
        (summary_df["Instrument"].isin(selected_instruments)) &
        (summary_df["Risk Manager"].isin(selected_rms))
    ].copy()

    return filtered_df