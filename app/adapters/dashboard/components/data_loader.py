"""
Загрузчик данных для Дашборда.

Этот модуль отвечает за сканирование директории логов, чтение файлов результатов
бэктестов (`.jsonl`) и сопоставление их с историческими рыночными данными (`.parquet`).

Ключевые функции:
1. **Агрегация:** Сбор разрозненных файлов логов в единый DataFrame.
2. **Аналитика:** Запуск `AnalysisSession` для расчета метрик (PnL, Sharpe, Drawdown) на лету.
3. **Кэширование:** Использование механизмов Streamlit для ускорения работы интерфейса.
"""

import os
import pandas as pd
import streamlit as st
import numpy as np
from typing import Dict, Any, Optional, List, Tuple

from app.infrastructure.storage.file_io import load_trades_from_file
from app.core.analysis.session import AnalysisSession
from app.shared.config import config

PATH_CONFIG = config.PATH_CONFIG
BACKTEST_CONFIG = config.BACKTEST_CONFIG


def _process_single_backtest_file(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Обрабатывает один файл лога сделок.

    Алгоритм:
    1. Читает лог сделок.
    2. Извлекает параметры стратегии (тикер, интервал) из первой записи.
    3. Находит соответствующий файл исторических данных (Parquet).
    4. Запускает `AnalysisSession` для расчета метрик портфеля и бенчмарка.

    Args:
        file_path (str): Полный путь к файлу `_trades.jsonl`.

    Returns:
        Optional[Dict[str, Any]]: Словарь с метриками для сводной таблицы
        или словарь с ключом "error", если обработка не удалась.
        Возвращает None, если файл пуст.
    """
    try:
        # --- 1. Загрузка сделок ---
        filename = os.path.basename(file_path)
        trades_df = load_trades_from_file(file_path)

        if trades_df.empty:
            return None  # Игнорируем пустые логи (бэктест без сделок)

        # --- 2. Извлечение метаданных ---
        # Предполагаем, что метаданные одинаковы для всех сделок в файле
        first_trade = trades_df.iloc[0]
        strategy_name = first_trade['strategy_name']
        exchange = first_trade['exchange']
        instrument = first_trade['instrument']
        interval = first_trade['interval']
        risk_manager = first_trade['risk_manager']

        # --- 3. Поиск исторических данных (для бенчмарка и графиков) ---
        data_path = os.path.join(
            PATH_CONFIG["DATA_DIR"],
            exchange,
            interval,
            f"{instrument.upper()}.parquet"
        )

        if not os.path.exists(data_path):
            # Если данные удалены, мы не сможем построить Equity Curve и сравнить с Bench
            raise FileNotFoundError(f"Файл исторических данных не найден: {data_path}")

        historical_data = pd.read_parquet(data_path)

        # --- 4. Запуск аналитического ядра ---
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

        # --- 5. Формирование строки результата ---
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
            # Защита от бесконечности для JSON/Display
            "Profit Factor": float(profit_factor) if np.isfinite(profit_factor) else 999.0,
            "Sharpe Ratio": portfolio_metrics.get("sharpe_ratio", 0),
            "Total Trades": int(portfolio_metrics.get("total_trades", 0)),
        }

    except Exception as e:
        # Возвращаем ошибку как данные, чтобы отобразить её в UI, а не крашить приложение
        return {"error": f"Ошибка обработки {os.path.basename(file_path)}: {e}"}


@st.cache_data
def load_all_backtests(logs_dir: str) -> Tuple[pd.DataFrame, List[str]]:
    """
    Сканирует папку логов и собирает сводную таблицу результатов.

    Использует кэширование Streamlit (`@st.cache_data`). Это означает, что
    функция не будет перезапускаться при каждом клике пользователя в интерфейсе,
    если аргумент `logs_dir` не изменился (или если кэш не был сброшен вручную).

    Args:
        logs_dir (str): Путь к директории с логами бэктестов.

    Returns:
        Tuple[pd.DataFrame, List[str]]:
            - DataFrame со сводной статистикой по всем успешным загрузкам.
            - Список сообщений об ошибках для файлов, которые не удалось прочитать.
    """
    all_results = []
    failed_files = []

    if not os.path.isdir(logs_dir):
        return pd.DataFrame(), [f"Директория логов не найдена: {logs_dir}"]

    # Рекурсивный поиск всех файлов _trades.jsonl
    log_files = []
    for root, _, files in os.walk(logs_dir):
        for filename in files:
            if filename.endswith("_trades.jsonl"):
                log_files.append(os.path.join(root, filename))

    if not log_files:
        return pd.DataFrame(), []

    # Визуализация прогресса загрузки
    progress_bar = st.progress(0, text="Анализ файлов бэктестов...")

    for i, file_path in enumerate(log_files):
        result_row = _process_single_backtest_file(file_path)

        if result_row:
            if "error" in result_row:
                failed_files.append(result_row["error"])
            else:
                all_results.append(result_row)

        # Обновляем прогресс-бар
        progress = (i + 1) / len(log_files)
        progress_bar.progress(progress, text=f"Обработка: {os.path.basename(file_path)}")

    progress_bar.empty()  # Скрываем бар после завершения

    if not all_results:
        return pd.DataFrame(), failed_files

    summary_df = pd.DataFrame(all_results)
    return summary_df, failed_files