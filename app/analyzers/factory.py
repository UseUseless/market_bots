import pandas as pd
import numpy as np
import logging

from config import EXCHANGE_SPECIFIC_CONFIG
from app.analyzers.metrics import MetricsCalculator, METRIC_CONFIG

logger = logging.getLogger(__name__)

def analyze_run_results(
        trades_df: pd.DataFrame,
        historical_data: pd.DataFrame,
        initial_capital: float,
        exchange: str,
) -> pd.Series:
    """
    Централизованная фабрика для анализа результатов одного запуска бэктеста.
    Рассчитывает полный набор метрик по сделкам и Buy & Hold.
    Всегда возвращает Series с полным и консистентным набором ключей.
    """
    metrics = {}

    # --- 1. Расчет Buy & Hold (делаем это всегда в начале) ---
    if not historical_data.empty:
        entry_price = historical_data['open'].iloc[0]
        exit_price = historical_data['close'].iloc[-1]

        if pd.notna(entry_price) and entry_price != 0:
            bh_pnl = (exit_price - entry_price) * (initial_capital / entry_price)
            bh_pnl_percent = (bh_pnl / initial_capital) * 100
        else:
            bh_pnl = 0.0
            bh_pnl_percent = 0.0
    else:
        bh_pnl = np.nan
        bh_pnl_percent = np.nan

    metrics['pnl_bh_abs'] = bh_pnl
    metrics['pnl_bh_pct'] = bh_pnl_percent

    # --- 2. Расчет метрик по сделкам ---
    if trades_df.empty or len(trades_df) < 2:
        # Заполняем все метрики по сделкам нулями
        for key in METRIC_CONFIG.keys():
            metrics[key] = 0.0

        metrics['pnl_abs'] = 0.0
        metrics['pnl_pct'] = 0.0
        metrics['total_trades'] = len(trades_df)

        # Немедленно возвращаем результат
        return pd.Series(metrics)

    # Этот блок выполняется только если сделок 2 или больше
    annualization_factor = EXCHANGE_SPECIFIC_CONFIG[exchange]["SHARPE_ANNUALIZATION_FACTOR"]
    calculator = MetricsCalculator(trades_df, initial_capital, annualization_factor)

    for metric_key in METRIC_CONFIG.keys():
        metrics[metric_key] = calculator.calculate(metric_key)

    # Используем уже рассчитанные значения
    metrics['pnl_abs'] = metrics['pnl']
    metrics['pnl_pct'] = (metrics['pnl'] / initial_capital) * 100
    metrics['total_trades'] = len(trades_df)

    # --- 3. Сборка итогового Series ---
    return pd.Series(metrics)