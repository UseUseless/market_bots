"""
Загрузчик данных для Дашборда.

Отвечает за чтение логов сделок и сопоставление их с историческими данными.
Рассчитан на актуальный формат логов, где все метаданные записаны внутри JSON.
"""

import os
import pandas as pd
import streamlit as st
import numpy as np
from typing import Dict, Any, Optional, List, Tuple

from app.infrastructure.files.file_io import load_trades_from_file
from app.core.analysis.session import AnalysisSession
from app.shared.config import config

PATH_CONFIG = config.PATH_CONFIG
BACKTEST_CONFIG = config.BACKTEST_CONFIG


def _process_single_backtest_file(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Обрабатывает один файл лога сделок.
    Ожидает, что файл содержит полные метаданные (exchange, strategy_name, etc.).
    """
    try:
        # 1. Загрузка сделок
        filename = os.path.basename(file_path)
        trades_df = load_trades_from_file(file_path)

        if trades_df.empty:
            return None

        # 2. Извлечение метаданных из первой сделки
        # Мы полагаемся на то, что BacktestEngine.run() записал эти поля.
        first_row = trades_df.iloc[0]

        strategy_name = first_row['strategy_name']
        exchange = first_row['exchange']
        instrument = first_row['instrument']
        interval = first_row['interval']
        risk_manager = first_row['risk_manager']

        # 3. Поиск исторических данных (для бенчмарка и графиков)
        data_path = os.path.join(
            PATH_CONFIG["DATA_DIR"],
            exchange,
            interval,
            f"{instrument.upper()}.parquet"
        )

        historical_data = pd.DataFrame()
        if os.path.exists(data_path):
            historical_data = pd.read_parquet(data_path)
        else:
            # Если данных нет (например, удалены), AnalysisSession обработает это штатно (пустой бенчмарк)
            pass

        # 4. Запуск аналитического ядра
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

        # 5. Формирование результата для таблицы
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
            "Profit Factor": float(profit_factor) if np.isfinite(profit_factor) else 999.0,
            "Sharpe Ratio": portfolio_metrics.get("sharpe_ratio", 0),
            "Total Trades": int(portfolio_metrics.get("total_trades", 0)),
        }

    except KeyError as e:
        # Если в файле нет нужного ключа — значит это старый или битый файл
        return {"error": f"Файл {os.path.basename(file_path)} имеет устаревший формат (нет поля {e})."}
    except Exception as e:
        return {"error": f"Ошибка обработки {os.path.basename(file_path)}: {e}"}


@st.cache_data
def load_all_backtests(logs_dir: str) -> Tuple[pd.DataFrame, List[str]]:
    """
    Сканирует папку логов и собирает сводную таблицу результатов.
    """
    all_results = []
    failed_files = []

    if not os.path.isdir(logs_dir):
        return pd.DataFrame(), [f"Директория логов не найдена: {logs_dir}"]

    # Рекурсивный поиск
    log_files = []
    for root, _, files in os.walk(logs_dir):
        for filename in files:
            if filename.endswith("_trades.jsonl"):
                log_files.append(os.path.join(root, filename))

    if not log_files:
        return pd.DataFrame(), []

    progress_bar = st.progress(0, text="Анализ файлов бэктестов...")

    for i, file_path in enumerate(log_files):
        result_row = _process_single_backtest_file(file_path)

        if result_row:
            if "error" in result_row:
                failed_files.append(result_row["error"])
            else:
                all_results.append(result_row)

        progress = (i + 1) / len(log_files)
        progress_bar.progress(progress, text=f"Обработка: {os.path.basename(file_path)}")

    progress_bar.empty()

    if not all_results:
        return pd.DataFrame(), failed_files

    summary_df = pd.DataFrame(all_results)
    return summary_df, failed_files