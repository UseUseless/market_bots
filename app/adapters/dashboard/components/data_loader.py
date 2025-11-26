import os
import pandas as pd
import streamlit as st
import numpy as np
from typing import Dict, Any, Optional, List, Tuple

from app.infrastructure.storage.file_io import load_trades_from_file
from app.core.analysis.session import AnalysisSession
from config import PATH_CONFIG, BACKTEST_CONFIG


def _process_single_backtest_file(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Обрабатывает один .jsonl файл с результатами бэктеста.

    Эта функция является сердцем загрузчика. Она:
    1. Загружает сделки.
    2. Находит и загружает соответствующие исторические данные.
    3. Запускает новый `AnalysisSession` для выполнения всех расчетов.
    4. Собирает ключевые метрики в единый словарь (строку для итоговой таблицы).
    5. Грациозно обрабатывает ошибки (например, отсутствие файла данных).

    :param file_path: Полный путь к файлу лога сделок (_trades.jsonl).
    :return: Словарь с ключевыми метриками или словарь с ошибкой.
    """
    try:
        # --- 1. Загрузка сделок ---
        filename = os.path.basename(file_path)
        trades_df = load_trades_from_file(file_path)
        if trades_df.empty:
            return None  # Пропускаем файлы без сделок

        # --- 2. Извлечение метаданных и поиск исторических данных ---
        first_trade = trades_df.iloc[0]
        strategy_name = first_trade['strategy_name']
        exchange = first_trade['exchange']
        instrument = first_trade['instrument']
        interval = first_trade['interval']
        risk_manager = first_trade['risk_manager']

        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval, f"{instrument.upper()}.parquet")
        if not os.path.exists(data_path):
            raise FileNotFoundError(f"Файл исторических данных не найден: {data_path}")

        historical_data = pd.read_parquet(data_path)

        # --- 3. Запуск сессии анализа (использование нашего нового модуля) ---
        analysis = AnalysisSession(
            trades_df=trades_df,
            historical_data=historical_data,
            initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"],
            exchange=exchange,
            interval=interval,
            risk_manager_type=risk_manager,
            strategy_name=strategy_name
        )

        portfolio_metrics = analysis.portfolio_metrics
        benchmark_metrics = analysis.benchmark_metrics

        # --- 4. Сборка итогового словаря ---
        profit_factor = portfolio_metrics.get("profit_factor", 0)

        return {
            "File Path": file_path,
            "File": filename,
            "Exchange": exchange,
            "Strategy": strategy_name,
            "Instrument": instrument,
            "Interval": interval,
            "Risk Manager": risk_manager,
            "PnL (Strategy %)": portfolio_metrics.get("pnl_pct", 0),
            "PnL (B&H %)": benchmark_metrics.get("pnl_pct", 0),
            "Win Rate (%)": portfolio_metrics.get("win_rate", 0) * 100,
            "Max Drawdown (%)": portfolio_metrics.get("max_drawdown", 0) * 100,
            "Profit Factor": float(profit_factor) if np.isfinite(profit_factor) else np.inf,
            "Sharpe Ratio": portfolio_metrics.get("sharpe_ratio", 0),
            "Total Trades": int(portfolio_metrics.get("total_trades", 0)),
        }
    except Exception as e:
        # Возвращаем ошибку в структурированном виде для отображения в UI
        return {"error": f"Не удалось обработать файл {os.path.basename(file_path)}: {e}"}


@st.cache_data
def load_all_backtests(logs_dir: str) -> Tuple[pd.DataFrame, List[str]]:
    """
    Сканирует директорию с логами, обрабатывает каждый файл и возвращает
    итоговый DataFrame со сводкой, а также список файлов, которые не удалось обработать.

    Ключевой элемент здесь - декоратор @st.cache_data. Он кэширует результат
    выполнения этой функции. Streamlit будет выполнять ее только один раз.
    При последующих взаимодействиях с виджетами (фильтрами, кнопками) результат
    будет мгновенно браться из кэша, что делает дашборд отзывчивым.

    :param logs_dir: Путь к папке с логами (например, 'logs/backtests').
    :return: Кортеж, содержащий (pd.DataFrame со сводкой, список строк с ошибками).
    """
    all_results = []
    failed_files = []

    if not os.path.isdir(logs_dir):
        return pd.DataFrame(), [f"Директория логов не найдена по пути: {logs_dir}"]

    # Собираем список файлов для обработки
    log_files = []
    for root, dirs, files in os.walk(logs_dir):
        for filename in files:
            if filename.endswith("_trades.jsonl"):
                # Добавляем полный путь к файлу в наш список
                log_files.append(os.path.join(root, filename))

    # Используем st.progress для наглядности, если файлов много
    progress_bar = st.progress(0, text="Загрузка и обработка результатов бэктестов...")

    for i, file_path in enumerate(log_files):
        result_row = _process_single_backtest_file(file_path)

        if result_row and "error" in result_row:
            failed_files.append(result_row["error"])
        elif result_row:
            all_results.append(result_row)

        # Обновляем прогресс-бар
        progress_bar.progress((i + 1) / len(log_files), text=f"Обработка файла: {os.path.basename(file_path)}")

    progress_bar.empty()  # Убираем прогресс-бар после завершения

    if not all_results:
        return pd.DataFrame(), failed_files

    summary_df = pd.DataFrame(all_results)
    return summary_df, failed_files