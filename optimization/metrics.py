import pandas as pd
import numpy as np
from typing import Dict, Any

# Здесь мы определяем все, что нужно системе знать о каждой метрике.
# Добавить новую метрику = добавить новую запись в этот словарь.
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


class MetricsCalculator:
    """
    Рассчитывает различные метрики производительности на основе DataFrame сделок.
    Оптимизирован для многократных вызовов: общие компоненты (equity, returns)
    рассчитываются только один раз при инициализации.
    """

    def __init__(self, trades_df: pd.DataFrame, initial_capital: float, annualization_factor: int = 252):
        if trades_df.empty or len(trades_df) < 2:
            # Если сделок нет или мало, все метрики считаем нулевыми (или худшими)
            self.is_valid = False
            return

        self.is_valid = True
        self.trades = trades_df.copy()
        self.initial_capital = initial_capital
        self.annualization_factor = annualization_factor

        # --- Предварительные расчеты, которые используются в нескольких метриках ---
        self.trades['cumulative_pnl'] = self.trades['pnl'].cumsum()
        self.trades['equity_curve'] = self.initial_capital + self.trades['cumulative_pnl']

        # Доходность от сделки к сделке
        self.returns = self.trades['equity_curve'].pct_change().dropna()
        if self.returns.empty:
            self.is_valid = False
            return

        # Просадка
        high_water_mark = self.trades['equity_curve'].cummax()
        drawdown = (self.trades['equity_curve'] - high_water_mark) / high_water_mark
        self.max_drawdown = abs(drawdown.min())

        # Профит-фактор
        self.gross_profit = self.trades[self.trades['pnl'] > 0]['pnl'].sum()
        self.gross_loss = abs(self.trades[self.trades['pnl'] < 0]['pnl'].sum())

        # Продолжительность периода
        start_date = pd.to_datetime(self.trades['entry_timestamp_utc'].iloc[0])
        end_date = pd.to_datetime(self.trades['exit_timestamp_utc'].iloc[-1])
        self.num_days = (end_date - start_date).days if (end_date - start_date).days > 1 else 1

    def calculate(self, metric_key: str) -> float:
        """Главный метод. Вызывает нужный расчет на основе ключа."""
        if not self.is_valid:
            return -1.0 if METRIC_CONFIG[metric_key]['direction'] == 'maximize' else 1e9

        method_map = {
            "calmar_ratio": self._calculate_calmar,
            "sharpe_ratio": self._calculate_sharpe,
            "sortino_ratio": self._calculate_sortino,
            "profit_factor": self._calculate_profit_factor,
            "pnl_to_drawdown": self._calculate_pnl_to_drawdown,
            "sqn": self._calculate_sqn,
            "pnl": lambda: self.trades['cumulative_pnl'].iloc[-1],
            "win_rate": lambda: (self.trades['pnl'] > 0).mean(),
            "max_drawdown": lambda: self.max_drawdown,
            "custom_metric": self._calculate_custom
        }

        if metric_key not in method_map:
            raise ValueError(f"Неизвестная метрика: {metric_key}")

        return method_map[metric_key]()

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