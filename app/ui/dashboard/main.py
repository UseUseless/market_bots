import streamlit as st
import pandas as pd

from .components.data_loader import load_all_backtests
from .components.sidebar import render_sidebar
from .components.detailed_view import render_detailed_view
from .components.comparison_view import render_comparison_view
from config import PATH_CONFIG

def style_summary_table(df: pd.DataFrame) -> "pd.io.formats.style.Styler":
    """
    Применяет кастомные стили к сводной таблице результатов для лучшей визуализации.

    Args:
        df: DataFrame для стилизации.

    Returns:
        Объект Styler с примененными стилями.
    """
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

def main():
    """
    Главная функция, которая собирает и отрисовывает всю страницу дашборда.
    """
    # 1. Базовая конфигурация страницы
    st.set_page_config(
        page_title="Market Bots Dashboard",
        page_icon="🤖",
        layout="wide",
    )
    st.title("🤖 Панель анализа торговых стратегий")

    # 2. Загрузка данных с помощью специализированного загрузчика
    summary_df, failed_files = load_all_backtests(PATH_CONFIG["LOGS_DIR"])

    # Обработка ошибок загрузки
    if failed_files:
        with st.expander("⚠️ Обнаружены проблемы при загрузке некоторых бэктестов"):
            for error_msg in failed_files:
                st.warning(error_msg)

    # Критическая проверка: если нет данных, дальнейшая работа бессмысленна
    if summary_df.empty:
        st.warning(
            "Не найдено ни одного корректно обработанного файла с результатами бэктестов (`_trades.jsonl`) в папке `logs/`."
        )
        st.info(
            "Убедитесь, что вы запустили хотя бы один бэктест и для него существуют исторические данные в папке `data/`."
        )
        return # Прерываем выполнение, чтобы избежать ошибок

    # 3. Отрисовка сайдбара и получение отфильтрованного DataFrame
    # Вся логика фильтрации инкапсулирована в этой функции
    filtered_df = render_sidebar(summary_df)

    # 4. Отображение главной сводной таблицы
    st.header("Сводная таблица результатов")
    if not filtered_df.empty:
        # Создаем индекс с 1 для удобства пользователя
        df_display = filtered_df.copy()
        df_display.index = pd.RangeIndex(start=1, stop=len(df_display) + 1, step=1)
        st.dataframe(style_summary_table(df_display), use_container_width=True)
    else:
        st.info("По выбранным фильтрам в сайдбаре не найдено ни одного бэктеста. Измените критерии фильтрации.")

    # 5. Отрисовка секции детального анализа
    # Этот компонент работает с отфильтрованными данными
    render_detailed_view(filtered_df)

    # 6. Отрисовка секции сравнительного анализа
    # Этот компонент должен иметь доступ ко всем данным для построения портфелей,
    # поэтому передаем в него исходный, нефильтрованный DataFrame.
    render_comparison_view(summary_df)


if __name__ == "__main__":
    main()