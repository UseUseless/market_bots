"""
Главная страница аналитического Дашборда (Streamlit Entry Point).

Этот модуль собирает воедино все UI-компоненты для анализа результатов бэктестов.
Он запускается как отдельное веб-приложение.

Структура страницы:
1. **Sidebar:** Фильтры по биржам, стратегиям и инструментам.
2. **Summary Table:** Сводная таблица всех проведенных тестов с тепловой картой метрик.
3. **Detailed View:** Глубокий анализ одного выбранного теста (графики, сделки).
4. **Comparison View:** Сравнение стратегий и моделирование портфелей.

Запуск:
    Обычно запускается через `launcher.py`, который настраивает окружение.
    При ручном запуске: `streamlit run app/adapters/dashboard/main.py` из корня проекта.
"""

import sys
import os
import streamlit as st
import pandas as pd

# --- Инициализация окружения (Path Fix) ---
# Этот блок должен идти ДО импортов из пакета `app`, чтобы Python мог найти модули,
# даже если скрипт запущен не из корня проекта.

def find_project_root(start_path, marker_file='launcher.py'):
    """
    Рекурсивно ищет корень проекта, поднимаясь вверх по директориям.
    """
    path = os.path.abspath(start_path)
    while True:
        if os.path.exists(os.path.join(path, marker_file)):
            return path
        parent_path = os.path.dirname(path)
        if parent_path == path:
            raise FileNotFoundError(f"Корень проекта с '{marker_file}' не найден.")
        path = parent_path

try:
    # Добавляем корень проекта в sys.path
    project_root = find_project_root(__file__)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
except FileNotFoundError as e:
    # Если запуск совсем кривой, используем stderr, т.к. st еще может быть не инициализирован
    print(f"CRITICAL PATH ERROR: {e}", file=sys.stderr)

from app.shared.logging_setup import setup_global_logging
setup_global_logging(mode='default')

# Теперь, когда sys.path настроен, можно импортировать внутренние модули
from app.adapters.dashboard.components.data_loader import load_all_backtests
from app.adapters.dashboard.components.sidebar import render_sidebar
from app.adapters.dashboard.components.detailed_view import render_detailed_view
from app.adapters.dashboard.components.comparison_view import render_comparison_view
from app.shared.config import config

PATH_CONFIG = config.PATH_CONFIG


def style_summary_table(df: pd.DataFrame) -> "pd.io.formats.style.Styler":
    """
    Применяет условное форматирование к сводной таблице результатов.

    Делает таблицу визуально понятной:
    - Зеленый градиент для хороших метрик (PnL, Sharpe).
    - Красный градиент для плохих (Drawdown).
    - Подсветка положительных/отрицательных значений.

    Args:
        df (pd.DataFrame): Исходные данные.

    Returns:
        Styler: Объект стилизации Pandas для отображения в Streamlit.
    """
    # Формат отображения чисел
    format_dict = {
        "PnL (Strategy %)": "{:.2f}%", "PnL (B&H %)": "{:.2f}%",
        "Win Rate (%)": "{:.2f}%", "Max Drawdown (%)": "{:.2f}%",
        "Profit Factor": "{:.2f}", "Sharpe Ratio": "{:.2f}",
    }

    # Применение стилей
    styler = df.style.format(format_dict, na_rep="-") \
        .background_gradient(cmap='Greens', subset=['PnL (Strategy %)', 'Profit Factor', 'Sharpe Ratio']) \
        .background_gradient(cmap='Reds', subset=['Max Drawdown (%)']) \
        .apply(lambda x: ['color: #d62728' if v < 0 else 'color: #2ca02c' for v in x],
               subset=['PnL (Strategy %)']) \
        .highlight_max(subset=['PnL (Strategy %)', 'Win Rate (%)', 'Profit Factor', 'Sharpe Ratio'],
                       color='rgba(95, 186, 125, 0.3)') \
        .highlight_min(subset=['Max Drawdown (%)'], color='rgba(214, 39, 40, 0.3)')

    return styler


def main():
    """
    Главная функция рендеринга страницы (Page Controller).
    """
    # 1. Настройка страницы
    st.set_page_config(
        page_title="Market Bots Dashboard",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    st.title("🤖 Панель анализа торговых стратегий")

    # 2. Загрузка данных (Model)
    # Используем кэшируемый загрузчик для скорости
    summary_df, failed_files = load_all_backtests(PATH_CONFIG["LOGS_BACKTEST_DIR"])

    # Отображение ошибок загрузки (если есть битые файлы)
    if failed_files:
        with st.expander("⚠️ Ошибки при загрузке логов"):
            for error_msg in failed_files:
                st.warning(error_msg)

    # Если данных нет вообще — прекращаем рендер
    if summary_df.empty:
        st.warning("Нет данных для отображения.")
        st.info(
            f"Не найдено файлов `_trades.jsonl` в директории `{PATH_CONFIG['LOGS_BACKTEST_DIR']}`.\n"
            "Запустите бэктест через `launcher.py` -> `Запустить бэктест`, чтобы сгенерировать данные."
        )
        return

    # 3. Сайдбар и Фильтрация (Controller)
    filtered_df = render_sidebar(summary_df)

    # 4. Сводная таблица (View)
    st.header("Сводная таблица результатов")
    if not filtered_df.empty:
        # Сбрасываем индекс для красоты (чтобы начинался с 1)
        df_display = filtered_df.copy()
        df_display.index = pd.RangeIndex(start=1, stop=len(df_display) + 1, step=1)

        st.dataframe(
            style_summary_table(df_display),
            use_container_width=True,
            height=300
        )
    else:
        st.info("По выбранным фильтрам данных не найдено.")

    # 5. Детальный анализ (View)
    st.divider()
    render_detailed_view(filtered_df)

    # 6. Сравнительный анализ (View)
    # Передаем полный DataFrame, так как для сравнения пользователю может
    # понадобиться выбрать стратегии, скрытые текущим фильтром сайдбара.
    render_comparison_view(summary_df)


if __name__ == "__main__":
    main()