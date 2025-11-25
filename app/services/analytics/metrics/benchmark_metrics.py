import pandas as pd
import numpy as np
from typing import Dict, Any

class BenchmarkMetricsCalculator:
    """
    Рассчитывает полный набор метрик для эталонной стратегии (например, Buy & Hold)
    на основе исторического временного ряда цен.
    """

    def __init__(self, historical_data: pd.DataFrame, initial_capital: float, annualization_factor: int = 252):
        if historical_data.empty:
            self.is_valid = False
            return

        self.is_valid = True
        self.data = historical_data.copy()
        self.initial_capital = initial_capital
        self.annualization_factor = annualization_factor

        # --- Ключевые расчеты ---
        entry_price = self.data['open'].iloc[0]
        if entry_price == 0:
            self.is_valid = False
            return

        quantity = self.initial_capital / entry_price
        self.equity_curve = self.data['close'] * quantity
        self.returns = self.equity_curve.pct_change().dropna()

    def calculate_all(self) -> Dict[str, Any]:
        """Рассчитывает все метрики для бенчмарка."""
        if not self.is_valid:
            return {
                'pnl_abs': 0.0, 'pnl_pct': 0.0, 'max_drawdown': 0.0, 'sharpe_ratio': 0.0
            }

        # PnL
        final_pnl = self.equity_curve.iloc[-1] - self.initial_capital

        # Max Drawdown
        high_water_mark = self.equity_curve.cummax()
        drawdown = (self.equity_curve - high_water_mark) / high_water_mark
        max_drawdown = abs(drawdown.min())

        # Sharpe Ratio
        sharpe_ratio = 0.0
        if not self.returns.empty and self.returns.std() != 0:
            sharpe_ratio = (self.returns.mean() / self.returns.std()) * np.sqrt(self.annualization_factor)

        return {
            'pnl_abs': final_pnl,
            'pnl_pct': (final_pnl / self.initial_capital) * 100,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio
        }