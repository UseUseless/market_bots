"""
Модуль расчета финансовых метрик.

Отвечает за вычисление показателей эффективности стратегии (Performance Metrics)
на основе истории сделок. Используется для генерации отчетов и в качестве
целевых функций для оптимизатора (Optuna).
"""

from typing import Dict, Any

import numpy as np
import pandas as pd

from app.core.analysis.constants import METRIC_CONFIG


class PortfolioMetricsCalculator:
    """
    Калькулятор метрик портфеля стратегии.

    Принимает DataFrame сделок и рассчитывает набор статистических показателей.
    Выполняет предварительные расчеты (Equity Curve, Returns) при инициализации
    для ускорения последующих запросов конкретных метрик.

    Attributes:
        trades (pd.DataFrame): История сделок с колонками 'pnl', 'entry_time', 'exit_time'.
        initial_capital (float): Стартовый депозит.
        annualization_factor (int): Число периодов в году (252 для акций, 365 для крипты).
        is_valid (bool): Флаг валидности данных (False, если сделок нет).
    """

    def __init__(self, trades_df: pd.DataFrame, initial_capital: float, annualization_factor: int = 252):
        """
        Инициализирует калькулятор и выполняет pre-calculation.

        Args:
            trades_df (pd.DataFrame): DataFrame закрытых сделок.
            initial_capital (float): Начальный капитал.
            annualization_factor (int): Коэффициент аннуализации (дней в году).
        """
        if trades_df.empty or len(trades_df) < 2:
            self.is_valid = False
            return

        self.is_valid = True
        self.trades = trades_df.copy()
        self.initial_capital = initial_capital
        self.annualization_factor = annualization_factor

        # --- Предварительные расчеты (Vectorized) ---

        # 1. Кривая капитала (Equity Curve)
        self.trades['cumulative_pnl'] = self.trades['pnl'].cumsum()
        self.trades['equity_curve'] = self.initial_capital + self.trades['cumulative_pnl']

        # 2. Доходности (Returns)
        self.returns = self.trades['equity_curve'].pct_change().dropna()
        if self.returns.empty:
            self.is_valid = False
            return

        # 3. Просадка (Drawdown)
        high_water_mark = self.trades['equity_curve'].cummax()
        drawdown = (self.trades['equity_curve'] - high_water_mark) / high_water_mark
        self.max_drawdown = abs(drawdown.min())

        # 4. Валовые показатели
        self.gross_profit = self.trades[self.trades['pnl'] > 0]['pnl'].sum()
        self.gross_loss = abs(self.trades[self.trades['pnl'] < 0]['pnl'].sum())

        # 5. Длительность теста (в днях)
        start_date = pd.to_datetime(self.trades['entry_timestamp_utc'].iloc[0])
        end_date = pd.to_datetime(self.trades['exit_timestamp_utc'].iloc[-1])
        delta = end_date - start_date
        self.num_days = delta.days if delta.days > 1 else 1

    def calculate(self, metric_key: str) -> float:
        """
        Вычисляет значение конкретной метрики по её ключу.

        Args:
            metric_key (str): Ключ метрики из `METRIC_CONFIG` (например, 'sharpe_ratio').

        Returns:
            float: Значение метрики.
        """
        if not self.is_valid:
            # Возвращаем "плохое" значение для оптимизатора, если данных нет
            direction = METRIC_CONFIG.get(metric_key, {}).get('direction', 'maximize')
            return -1.0 if direction == 'maximize' else 1e9

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
        """
        Рассчитывает полный набор доступных метрик.

        Returns:
            Dict[str, Any]: Словарь {metric_name: value}.
        """
        if not self.is_valid:
            all_metrics = {key: 0.0 for key in METRIC_CONFIG.keys()}
            all_metrics.update({'pnl_abs': 0.0, 'pnl_pct': 0.0, 'total_trades': 0})
            return all_metrics

        results = {key: self.calculate(key) for key in METRIC_CONFIG.keys()}

        # Доп. метрики для отчетов
        results['pnl_abs'] = results['pnl']
        results['pnl_pct'] = (results['pnl'] / self.initial_capital) * 100 if self.initial_capital > 0 else 0.0
        results['total_trades'] = len(self.trades)

        return results

    def _calculate_sharpe(self) -> float:
        """
        Sharpe Ratio = Mean_Return / Std_Dev * sqrt(Periods).
        Показывает доходность на единицу риска (волатильности).
        """
        if self.returns.std() == 0: return 0.0
        return (self.returns.mean() / self.returns.std()) * np.sqrt(self.annualization_factor)

    def _calculate_sortino(self) -> float:
        """
        Sortino Ratio. Аналог Шарпа, но учитывает только волатильность убытков (Downside Risk).
        """
        downside_returns = self.returns[self.returns < 0]
        if downside_returns.empty:
            # Нет убыточных периодов — идеально
            return 9999.0 if self.returns.mean() > 0 else 0.0

        downside_std = downside_returns.std()
        if downside_std == 0:
            return 9999.0 if self.returns.mean() > 0 else 0.0

        return (self.returns.mean() / downside_std) * np.sqrt(self.annualization_factor)

    def _calculate_calmar(self) -> float:
        """
        Calmar Ratio = Annualized Return / Max Drawdown.
        Показывает доходность относительно худшей просадки.
        """
        total_return = self.trades['cumulative_pnl'].iloc[-1] / self.initial_capital

        # Аннуализация доходности: (1 + R)^(365/days) - 1
        annualized_return = ((1 + total_return) ** (365.0 / self.num_days)) - 1 if self.num_days > 0 else 0.0

        if self.max_drawdown == 0:
            return 9999.0 if annualized_return > 0 else 0.0

        return annualized_return / self.max_drawdown

    def _calculate_profit_factor(self) -> float:
        """
        Profit Factor = Gross Profit / Gross Loss.
        """
        if self.gross_loss == 0:
            return 9999.0 if self.gross_profit > 0 else 1.0
        return self.gross_profit / self.gross_loss

    def _calculate_pnl_to_drawdown(self) -> float:
        """
        Total PnL / Max Drawdown (в деньгах).
        Простая метрика: сколько рублей прибыли приходится на рубль просадки.
        """
        if self.max_drawdown == 0:
            return 9999.0 if self.trades['pnl'].sum() > 0 else 0.0

        total_pnl = self.trades['cumulative_pnl'].iloc[-1]
        max_dd_money = self.max_drawdown * self.initial_capital

        return total_pnl / max_dd_money

    def _calculate_sqn(self) -> float:
        """
        System Quality Number (Van Tharp).
        SQN = sqrt(N) * (Mean / Std).
        Оценивает качество системы с учетом количества сделок.
        """
        if self.trades['pnl'].std() == 0: return 0.0
        return np.sqrt(len(self.trades)) * (self.trades['pnl'].mean() / self.trades['pnl'].std())

    def _calculate_custom(self) -> float:
        """
        Кастомная метрика: (PF * WinRate) / MaxDD.
        Попытка сбалансировать прибыльность, точность и риск.
        """
        pf = self._calculate_profit_factor()
        wr = (self.trades['pnl'] > 0).mean()

        if self.max_drawdown == 0:
            return 9999.0 if pf > 1 and wr > 0 else 0.0

        # Нормализуем PF (макс 10), чтобы он не перетягивал вес
        normalized_pf = min(pf, 10.0)

        return (normalized_pf * wr) / self.max_drawdown


class BenchmarkMetricsCalculator:
    """
    Калькулятор метрик для эталонной стратегии (Buy & Hold).

    Рассчитывает доходность простого удержания актива за тот же период времени.
    """

    def __init__(self, historical_data: pd.DataFrame, initial_capital: float, annualization_factor: int = 252):
        """
        Args:
            historical_data (pd.DataFrame): Данные котировок (Close).
            initial_capital (float): Стартовый капитал.
        """
        if historical_data.empty:
            self.is_valid = False
            return

        self.is_valid = True
        self.data = historical_data.copy()
        self.initial_capital = initial_capital
        self.annualization_factor = annualization_factor

        # Симуляция покупки на всю котлету на первой свече
        entry_price = self.data['open'].iloc[0]
        if entry_price == 0:
            self.is_valid = False
            return

        quantity = self.initial_capital / entry_price

        # Кривая капитала = Цена * Кол-во
        self.equity_curve = self.data['close'] * quantity
        self.returns = self.equity_curve.pct_change().dropna()

    def calculate_all(self) -> Dict[str, Any]:
        """
        Возвращает основные метрики бенчмарка.
        """
        if not self.is_valid:
            return {'pnl_abs': 0.0, 'pnl_pct': 0.0, 'max_drawdown': 0.0, 'sharpe_ratio': 0.0}

        # PnL
        final_pnl = self.equity_curve.iloc[-1] - self.initial_capital

        # Max Drawdown
        high_water_mark = self.equity_curve.cummax()
        drawdown = (self.equity_curve - high_water_mark) / high_water_mark
        max_drawdown = abs(drawdown.min())

        # Sharpe
        sharpe_ratio = 0.0
        if not self.returns.empty and self.returns.std() != 0:
            sharpe_ratio = (self.returns.mean() / self.returns.std()) * np.sqrt(self.annualization_factor)

        return {
            'pnl_abs': final_pnl,
            'pnl_pct': (final_pnl / self.initial_capital) * 100,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio
        }