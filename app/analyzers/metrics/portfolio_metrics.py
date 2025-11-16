import pandas as pd
import numpy as np
from typing import Dict, Any


METRIC_CONFIG: Dict[str, Dict[str, Any]] = {
    "calmar_ratio": {
        "name": "Calmar Ratio",
        "direction": "maximize",
        "description": "Годовая доходность / Макс. просадка. Идеально для минимизации просадок."
    },
    "sharpe_ratio": {
        "name": "Sharpe Ratio",
        "direction": "maximize",
        "description": "Доходность / Волатильность. Классический универсальный выбор."
    },
    "sortino_ratio": {
        "name": "Sortino Ratio",
        "direction": "maximize",
        "description": "Доходность / Волатильность убытков. Улучшенный Шарп."
    },
    "profit_factor": {
        "name": "Profit Factor",
        "direction": "maximize",
        "description": "Суммарная прибыль / Суммарный убыток. Просто и понятно."
    },
    "pnl_to_drawdown": {
        "name": "PnL / Max Drawdown",
        "direction": "maximize",
        "description": "Общий PnL / Макс. просадка. Интуитивно понятная метрика."
    },
    "sqn": {
        "name": "SQN (System Quality Number)",
        "direction": "maximize",
        "description": "Комплексная метрика качества системы от Вана Тарпа."
    },
    "pnl": {
        "name": "Total PnL (Чистая прибыль)",
        "direction": "maximize",
        "description": "Максимизация итоговой чистой прибыли."
    },
    "win_rate": {
        "name": "Win Rate (Процент прибыльных сделок)",
        "direction": "maximize",
        "description": "Максимизация доли прибыльных сделок."
    },
    "max_drawdown": {
        "name": "Max Drawdown (Макс. просадка)",
        "direction": "minimize",
        "description": "Минимизация максимальной просадки капитала."
    },
    # КАСТОМНАЯ МЕТРИКА
    "custom_metric": {
        "name": "Custom (PF * WR / MDD)",
        "direction": "maximize",
        "description": "Наша уникальная функция: (Profit Factor * Win Rate) / Max Drawdown."
    }
}

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
            # ... (все методы расчета _calculate_sharpe, _calculate_calmar и т.д.) ...
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
            all_metrics['total_trades'] = len(self.trades)
            return all_metrics

        results = {key: self.calculate(key) for key in METRIC_CONFIG.keys()}

        # Добавляем базовые метрики, которые не входят в основной конфиг
        results['pnl_abs'] = results['pnl']
        results['pnl_pct'] = (results['pnl'] / self.initial_capital) * 100
        results['total_trades'] = len(self.trades)

        return results

    def _calculate_sharpe(self) -> float:
        if self.returns.std() == 0: return 0.0
        return (self.returns.mean() / self.returns.std()) * np.sqrt(self.annualization_factor)

    def _calculate_sortino(self) -> float:
        downside_returns = self.returns[self.returns < 0]
        downside_std = downside_returns.std()
        if downside_std == 0: return np.inf if self.returns.mean() > 0 else 0.0
        return (self.returns.mean() / downside_std) * np.sqrt(self.annualization_factor)

    def _calculate_calmar(self) -> float:
        if self.max_drawdown == 0: return np.inf if self.trades['pnl'].sum() > 0 else 0.0
        total_return = self.trades['cumulative_pnl'].iloc[-1] / self.initial_capital
        annualized_return = ((1 + total_return) ** (365.0 / self.num_days)) - 1
        return annualized_return / self.max_drawdown

    def _calculate_profit_factor(self) -> float:
        if self.gross_loss == 0: return np.inf if self.gross_profit > 0 else 1.0
        return self.gross_profit / self.gross_loss

    def _calculate_pnl_to_drawdown(self) -> float:
        if self.max_drawdown == 0: return np.inf if self.trades['pnl'].sum() > 0 else 0.0
        total_pnl = self.trades['cumulative_pnl'].iloc[-1]
        return total_pnl / (self.max_drawdown * self.initial_capital)

    def _calculate_sqn(self) -> float:
        if self.trades['pnl'].std() == 0: return 0.0
        return np.sqrt(len(self.trades)) * (self.trades['pnl'].mean() / self.trades['pnl'].std())

    def _calculate_custom(self) -> float:
        pf = self._calculate_profit_factor()
        wr = (self.trades['pnl'] > 0).mean()
        if self.max_drawdown == 0: return np.inf
        # Нормализуем PF, чтобы он не доминировал слишком сильно
        # (например, ограничиваем сверху значением 10)
        normalized_pf = min(pf, 10.0)
        return (normalized_pf * wr) / self.max_drawdown