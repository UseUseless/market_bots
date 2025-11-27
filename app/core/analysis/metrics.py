from typing import Dict, Any

import numpy as np
import pandas as pd

from app.core.analysis.constants import METRIC_CONFIG


class PortfolioMetricsCalculator:
    """
    Рассчитывает различные метрики производительности на основе DataFrame сделок.
    Оптимизирован для многократных вызовов: общие компоненты (equity, returns)
    рассчитываются только один раз при инициализации.
    """

    def __init__(self, trades_df: pd.DataFrame, initial_capital: float, annualization_factor: int = 252):
        if trades_df.empty or len(trades_df) < 2:
            self.is_valid = False
            return

        self.is_valid = True
        self.trades = trades_df.copy()
        self.initial_capital = initial_capital
        self.annualization_factor = annualization_factor

        # --- Предварительные расчеты, которые используются в нескольких метриках ---
        self.trades['cumulative_pnl'] = self.trades['pnl'].cumsum()
        self.trades['equity_curve'] = self.initial_capital + self.trades['cumulative_pnl']
        self.returns = self.trades['equity_curve'].pct_change().dropna()
        if self.returns.empty:
            self.is_valid = False
            return

        high_water_mark = self.trades['equity_curve'].cummax()
        drawdown = (self.trades['equity_curve'] - high_water_mark) / high_water_mark
        self.max_drawdown = abs(drawdown.min())
        self.gross_profit = self.trades[self.trades['pnl'] > 0]['pnl'].sum()
        self.gross_loss = abs(self.trades[self.trades['pnl'] < 0]['pnl'].sum())
        start_date = pd.to_datetime(self.trades['entry_timestamp_utc'].iloc[0])
        end_date = pd.to_datetime(self.trades['exit_timestamp_utc'].iloc[-1])
        self.num_days = (end_date - start_date).days if (end_date - start_date).days > 1 else 1

    def calculate(self, metric_key: str) -> float:
        """Главный метод. Вызывает нужный расчет на основе ключа."""
        if not self.is_valid:
            return -1.0 if METRIC_CONFIG[metric_key]['direction'] == 'maximize' else 1e9

        method_map = {
            "sharpe_ratio": self._calculate_sharpe,
            "sortino_ratio": self._calculate_sortino,
            "calmar_ratio": self._calculate_calmar,
            "profit_factor": self._calculate_profit_factor,
            "pnl_to_drawdown": self._calculate_pnl_to_drawdown,
            "sqn": self._calculate_sqn,
            "custom_metric": self._calculate_custom,
            "pnl": lambda: self.trades['pnl'].sum(),
            "win_rate": lambda: (self.trades['pnl'] > 0).mean(),
            "max_drawdown": lambda: self.max_drawdown,
        }

        if metric_key not in method_map:
            raise ValueError(f"Неизвестная метрика: {metric_key}")
        return method_map[metric_key]()

    def calculate_all(self) -> Dict[str, Any]:
        """Рассчитывает все доступные метрики и возвращает их в виде словаря."""
        if not self.is_valid:
            # Возвращаем словарь с нулевыми значениями по умолчанию
            all_metrics = {key: 0.0 for key in METRIC_CONFIG.keys()}
            all_metrics['pnl_abs'] = 0.0
            all_metrics['pnl_pct'] = 0.0
            all_metrics['total_trades'] = 0
            return all_metrics

        results = {key: self.calculate(key) for key in METRIC_CONFIG.keys()}

        # Добавляем базовые метрики, которые не входят в основной конфиг
        results['pnl_abs'] = results['pnl']
        results['pnl_pct'] = (results['pnl'] / self.initial_capital) * 100 if self.initial_capital > 0 else 0.0
        results['total_trades'] = len(self.trades)

        return results

    def _calculate_sharpe(self) -> float:
        if self.returns.std() == 0: return 0.0
        return (self.returns.mean() / self.returns.std()) * np.sqrt(self.annualization_factor)

    def _calculate_sortino(self) -> float:
        downside_returns = self.returns[self.returns < 0]
        if downside_returns.empty:
            # Если нет убыточных сделок, волатильность убытков равна 0.
            # Возвращаем большое число, если доходность положительная, иначе 0.
            return 9999.0 if self.returns.mean() > 0 else 0.0
        downside_std = downside_returns.std()
        if downside_std == 0:
             return 9999.0 if self.returns.mean() > 0 else 0.0
        return (self.returns.mean() / downside_std) * np.sqrt(self.annualization_factor)

    def _calculate_calmar(self) -> float:
        total_return = self.trades['cumulative_pnl'].iloc[-1] / self.initial_capital
        annualized_return = ((1 + total_return) ** (365.0 / self.num_days)) - 1 if self.num_days > 0 else 0.0
        if self.max_drawdown == 0:
            # Если просадки не было, это идеальный результат.
            # Возвращаем большое число, если была прибыль, иначе 0.
            return 9999.0 if annualized_return > 0 else 0.0
        return annualized_return / self.max_drawdown

    def _calculate_profit_factor(self) -> float:
        if self.gross_loss == 0:
            # Если убытков не было, это идеальный результат.
            # Возвращаем большое число, если была прибыль, иначе 1 (нейтрально).
            return 9999.0 if self.gross_profit > 0 else 1.0
        return self.gross_profit / self.gross_loss

    def _calculate_pnl_to_drawdown(self) -> float:
        if self.max_drawdown == 0:
            # Если просадки не было, это идеальный результат.
            return 9999.0 if self.trades['pnl'].sum() > 0 else 0.0
        total_pnl = self.trades['cumulative_pnl'].iloc[-1]
        return total_pnl / (self.max_drawdown * self.initial_capital)

    def _calculate_sqn(self) -> float:
        if self.trades['pnl'].std() == 0: return 0.0
        return np.sqrt(len(self.trades)) * (self.trades['pnl'].mean() / self.trades['pnl'].std())

    def _calculate_custom(self) -> float:
        pf = self._calculate_profit_factor()
        wr = (self.trades['pnl'] > 0).mean()
        if self.max_drawdown == 0:
            return 9999.0 if pf > 1 and wr > 0 else 0.0
        # Нормализуем PF, чтобы он не доминировал слишком сильно
        # (например, ограничиваем сверху значением 10)
        normalized_pf = min(pf, 10.0)
        return (normalized_pf * wr) / self.max_drawdown


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
