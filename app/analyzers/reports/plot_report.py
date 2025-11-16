import logging
import os
from typing import Dict, Optional, Any

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt

from app.analyzers.metrics.portfolio_metrics import METRIC_CONFIG
from config import EXCHANGE_SPECIFIC_CONFIG

logger = logging.getLogger(__name__)


class PlotReportGenerator:
    """
    Отвечает исключительно за создание и сохранение графического отчета (.png)
    на основе УЖЕ РАССЧИТАННЫХ метрик и данных.
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

        self.portfolio_metrics = portfolio_metrics
        self.benchmark_metrics = benchmark_metrics
        self.portfolio_equity_curve = portfolio_equity_curve
        self.benchmark_equity_curve = benchmark_equity_curve
        self.initial_capital = initial_capital
        self.report_filename = report_filename
        self.report_dir = report_dir
        self.metadata = metadata
        os.makedirs(self.report_dir, exist_ok=True)

    def _format_metrics_for_display(self) -> Dict[str, str]:
        """Форматирует числовые метрики в строки для красивого вывода на графике."""
        pnl_abs = self.portfolio_metrics.get('pnl_abs', 0)
        pnl_pct = self.portfolio_metrics.get('pnl_pct', 0)
        pnl_bh_abs = self.benchmark_metrics.get('pnl_abs', 0)
        pnl_bh_pct = self.benchmark_metrics.get('pnl_pct', 0)
        profit_factor = self.portfolio_metrics.get('profit_factor', 0)

        profit_factor_str = f"{profit_factor:.2f}" if np.isfinite(profit_factor) else "inf"

        exchange = self.metadata.get("exchange", "tinkoff")
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
        """Создает и сохраняет графический отчет."""
        display_metrics = self._format_metrics_for_display()

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

        if not self.portfolio_equity_curve.empty:
            ax.plot(self.portfolio_equity_curve.index, self.portfolio_equity_curve.values,
                    label='Strategy Equity Curve', color='blue', lw=2)

        if not self.benchmark_equity_curve.empty:
            ax.plot(self.benchmark_equity_curve.index, self.benchmark_equity_curve.values, label='Buy & Hold Benchmark',
                    color='gray', linestyle='--', lw=1.5)

        ax.set_title(f"Backtest Results: {self.metadata.get('strategy_name', 'N/A')} on {self.report_filename}",
                     fontsize=16)
        ax.set_xlabel("Date")
        fig.autofmt_xdate()
        ax.set_ylabel("Capital")
        ax.legend()

        report_text = "\n".join([f"{key}: {value}" for key, value in display_metrics.items()])
        full_report_text = report_text + wfo_text_block

        ax.text(0.02, 0.98, full_report_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                fontfamily='monospace')

        full_path = os.path.join(self.report_dir, f"{self.report_filename}.png")
        plt.savefig(full_path)
        plt.close(fig)
        logger.info(f"Графический отчет сохранен в: {full_path}")
