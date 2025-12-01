"""
Модуль генерации графических отчетов (Plotting).

Отвечает за визуализацию кривой капитала (Equity Curve) стратегии в сравнении
с эталонной стратегией Buy & Hold. Генерирует файл `.png` с графиком и ключевыми метриками.

Особенности визуализации:
Используется отрисовка по целочисленным индексам вместо дат, чтобы скрыть "дырки"
в данных (выходные дни, ночи), делая график непрерывным и удобным для анализа.
"""

import logging
import os
from typing import Dict, Optional, Any

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
import matplotlib.ticker as ticker

from app.core.analysis.constants import METRIC_CONFIG
from app.shared.primitives import ExchangeType
from app.shared.config import config

EXCHANGE_SPECIFIC_CONFIG = config.EXCHANGE_SPECIFIC_CONFIG

logger = logging.getLogger(__name__)


class PlotReportGenerator:
    """
    Генератор графиков Matplotlib.

    Строит линейный график доходности, накладывает на него статистику
    и сохраняет результат в файл.

    Attributes:
        portfolio_equity_curve (pd.Series): Ряд капитала стратегии.
        benchmark_equity_curve (pd.Series): Ряд капитала бенчмарка.
        report_filename (str): Имя файла для сохранения.
        report_dir (str): Папка для сохранения.
    """

    def __init__(self,
                 portfolio_metrics: Dict[str, Any],
                 benchmark_metrics: Dict[str, Any],
                 portfolio_equity_curve: pd.Series,
                 benchmark_equity_curve: pd.Series,
                 initial_capital: float,
                 report_filename: str,
                 report_dir: str,
                 metadata: Dict[str, str]):
        """
        Инициализирует генератор.

        Args:
            portfolio_metrics (Dict): Рассчитанные метрики стратегии.
            benchmark_metrics (Dict): Рассчитанные метрики бенчмарка.
            portfolio_equity_curve (pd.Series): Временной ряд капитала стратегии.
            benchmark_equity_curve (pd.Series): Временной ряд капитала бенчмарка.
            initial_capital (float): Стартовый капитал (для масштабирования).
            report_filename (str): Базовое имя файла.
            report_dir (str): Папка назначения.
            metadata (Dict): Инфо о стратегии и инструменте.
        """
        self.portfolio_metrics = portfolio_metrics
        self.benchmark_metrics = benchmark_metrics
        self.portfolio_equity_curve = portfolio_equity_curve
        self.benchmark_equity_curve = benchmark_equity_curve
        self.initial_capital = initial_capital
        self.report_filename = report_filename
        self.report_dir = report_dir
        self.metadata = metadata

        # Гарантируем существование директории
        os.makedirs(self.report_dir, exist_ok=True)

    def _format_metrics_for_display(self) -> Dict[str, str]:
        """
        Форматирует метрики для отображения в текстовом блоке на графике.

        Returns:
            Dict[str, str]: Словарь {Метрика: Красивое_Значение}.
        """
        pnl_abs = self.portfolio_metrics.get('pnl_abs', 0)
        pnl_pct = self.portfolio_metrics.get('pnl_pct', 0)
        pnl_bh_abs = self.benchmark_metrics.get('pnl_abs', 0)
        pnl_bh_pct = self.benchmark_metrics.get('pnl_pct', 0)
        profit_factor = self.portfolio_metrics.get('profit_factor', 0)

        profit_factor_str = f"{profit_factor:.2f}" if np.isfinite(profit_factor) else "inf"

        exchange = self.metadata.get("exchange", ExchangeType.TINKOFF)
        annual_factor = EXCHANGE_SPECIFIC_CONFIG.get(exchange, {}).get("SHARPE_ANNUALIZATION_FACTOR", 252)

        return {
            "Interval": self.metadata.get("interval", "N/A"),
            "Risk Manager": self.metadata.get("risk_manager_type", "N/A"),
            "---": "---",
            "Total PnL (Strategy)": f"{pnl_abs:.2f} ({pnl_pct:.2f}%)",
            "Total PnL (Buy & Hold)": f"{pnl_bh_abs:.2f} ({pnl_bh_pct:.2f}%)",
            "--- ": "--- ",
            "Win Rate": f"{self.portfolio_metrics.get('win_rate', 0) * 100:.2f}%",
            "Max Drawdown": f"{self.portfolio_metrics.get('max_drawdown', 0) * 100:.2f}%",
            "Profit Factor": profit_factor_str,
            "Sharpe Ratio": f"{self.portfolio_metrics.get('sharpe_ratio', 0):.2f} (ann. by {annual_factor}d)",
            "Total Trades": int(self.portfolio_metrics.get('total_trades', 0))
        }

    def generate(self, wfo_results: Optional[Dict[str, float]] = None):
        """
        Строит и сохраняет график.

        Использует технику "Index-based plotting" для устранения разрывов во времени
        (выходные дни, неторговые часы). Вместо оси времени используется ось индексов (0..N),
        а даты подставляются через `FuncFormatter`.

        Args:
            wfo_results (Optional[Dict]): Результаты WFO (если применимо) для отображения на графике.
        """
        display_metrics = self._format_metrics_for_display()

        # Формирование блока текста с результатами WFO (если есть)
        wfo_text_block = ""
        if wfo_results:
            target_metric = next(iter(wfo_results.keys()), None)
            target_metric_name = METRIC_CONFIG.get(target_metric, {}).get('name', target_metric)
            oos_metrics_lines = [f"{METRIC_CONFIG.get(k, {}).get('name', k):<20}: {v:.3f}" for k, v in
                                 wfo_results.items()]
            wfo_text_block = (
                    f"\n--- WFO Final OOS Results ---\n"
                    f"Target Metric: {target_metric_name}\n"
                    f"---------------------------------\n" + "\n".join(oos_metrics_lines)
            )

        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(15, 7))

        # 1. Выравнивание индексов
        # Используем индекс бенчмарка как эталонный (он содержит все свечи периода)
        if not self.benchmark_equity_curve.empty:
            full_time_index = self.benchmark_equity_curve.index
        else:
            full_time_index = self.portfolio_equity_curve.index

        # 2. Подготовка данных стратегии
        if not self.portfolio_equity_curve.empty:
            # Растягиваем кривую стратегии на полный диапазон времени (forward fill)
            aligned_portfolio_curve = self.portfolio_equity_curve.reindex(full_time_index, method='ffill')
            # Заполняем начало (до первой сделки) стартовым капиталом
            aligned_portfolio_curve = aligned_portfolio_curve.fillna(self.initial_capital)
            portfolio_values = aligned_portfolio_curve.values
        else:
            # Если сделок не было — прямая линия
            portfolio_values = np.full(len(full_time_index), self.initial_capital)

        # 3. Подготовка данных бенчмарка
        if not self.benchmark_equity_curve.empty:
            benchmark_values = self.benchmark_equity_curve.values
        else:
            benchmark_values = np.array([])

        # 4. Отрисовка по целочисленным индексам (скрываем выходные)
        x_indices = np.arange(len(full_time_index))

        if len(benchmark_values) > 0:
            ax.plot(x_indices, benchmark_values, label='Buy & Hold Benchmark',
                    color='gray', alpha=0.5, lw=1.5)

        if len(portfolio_values) > 0:
            ax.plot(x_indices, portfolio_values,
                    label='Strategy Equity Curve', color='blue', lw=2)

        # 5. Форматирование оси X (индексы -> даты)
        def format_date(x, pos=None):
            thisind = np.clip(int(x + 0.5), 0, len(full_time_index) - 1)
            return full_time_index[thisind].strftime('%Y-%m-%d')

        ax.xaxis.set_major_formatter(ticker.FuncFormatter(format_date))
        ax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=10))

        ax.set_title(f"Backtest Results: {self.metadata.get('strategy_name', 'N/A')} on {self.report_filename}",
                     fontsize=16)
        ax.set_xlabel("Date")
        fig.autofmt_xdate()
        ax.set_ylabel("Capital")
        ax.legend()

        # 6. Добавление текстового блока с метриками
        report_text = "\n".join([f"{key}: {value}" for key, value in display_metrics.items()])
        full_report_text = report_text + wfo_text_block

        ax.text(0.02, 0.98, full_report_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                fontfamily='monospace')

        # Сохранение и закрытие
        full_path = os.path.join(self.report_dir, f"{self.report_filename}.png")
        plt.savefig(full_path)
        plt.close(fig)
        logger.info(f"Графический отчет сохранен в: {full_path}")