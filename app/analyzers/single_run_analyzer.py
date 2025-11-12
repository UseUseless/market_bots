import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import logging
from rich.console import Console
from rich.table import Table
from typing import Dict, Optional

from config import PATH_CONFIG, EXCHANGE_SPECIFIC_CONFIG
from app.analyzers.metrics import METRIC_CONFIG

logger = logging.getLogger(__name__)

class SingleRunAnalyzer:
    """
    Анализирует и представляет результаты ОДНОГО бэктеста.
    Отвечает за генерацию графического (.png) и консольного отчета.
    Получает все метрики в уже рассчитанном виде.
    """

    def __init__(
            self,
            metrics: pd.Series,
            trades_df: pd.DataFrame,
            historical_data: pd.DataFrame,
            initial_capital: float,
            interval: str,
            risk_manager_type: str,
            exchange: str,
            report_dir: str = PATH_CONFIG["REPORTS_BACKTEST_DIR"]
    ):
        self.metrics = metrics
        self.trades_df = trades_df
        self.historical_data = historical_data
        self.initial_capital = initial_capital
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.exchange = exchange
        self.report_dir = report_dir
        os.makedirs(self.report_dir, exist_ok=True)

    def _format_metrics_for_display(self) -> Dict[str, str]:
        """Форматирует числовые метрики в строки для красивого вывода."""
        pnl_abs = self.metrics.get('pnl_abs', 0)
        pnl_pct = self.metrics.get('pnl_pct', 0)
        pnl_bh_abs = self.metrics.get('pnl_bh_abs', 0)
        pnl_bh_pct = self.metrics.get('pnl_bh_pct', 0)
        profit_factor = self.metrics.get('profit_factor', 0)

        profit_factor_str = f"{profit_factor:.2f}" if np.isfinite(profit_factor) else "inf"

        annual_factor = EXCHANGE_SPECIFIC_CONFIG[self.exchange]["SHARPE_ANNUALIZATION_FACTOR"]

        return {
            "Interval": self.interval,
            "Risk Manager Type": self.risk_manager_type,
            "---": "---",
            "Total PnL (Strategy)": f"{pnl_abs:.2f} ({pnl_pct:.2f}%)",
            "Total PnL (Buy & Hold)": f"{pnl_bh_abs:.2f} ({pnl_bh_pct:.2f}%)",
            "--- ": "--- ",
            "Win Rate": f"{self.metrics.get('win_rate', 0) * 100:.2f}%",
            "Max Drawdown": f"{self.metrics.get('max_drawdown', 0) * 100:.2f}%",
            "Profit Factor": profit_factor_str,
            "Sharpe Ratio": f"{self.metrics.get('sharpe_ratio', 0):.2f} (ann. by {annual_factor}d)",
            "Total Trades": int(self.metrics.get('total_trades', 0))
        }

    def generate_report(self, report_filename: str, wfo_results: Optional[Dict[str, float]] = None):
        """Создает и сохраняет отчет с графиком и метриками."""
        display_metrics = self._format_metrics_for_display()

        wfo_text_block = ""
        if wfo_results:
            # Получаем красивое имя целевой метрики (берем первую, если их несколько)
            target_metric = next(iter(wfo_results.keys()), None)
            target_metric_name = METRIC_CONFIG.get(target_metric, {}).get('name', target_metric)

            oos_metrics_lines = []
            for key, value in wfo_results.items():
                metric_name = METRIC_CONFIG.get(key, {}).get('name', key)
                prefix = ">> " if key == target_metric else "   "
                oos_metrics_lines.append(f"{prefix}{metric_name:<20}: {value:.3f}")

            wfo_text_block = (
                    f"\n--- WFO Final OOS Results ---\n"
                    f"Target Metric: {target_metric_name}\n"
                    f"---------------------------------\n"
                    + "\n".join(oos_metrics_lines)
            )

        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(15, 7))

        # Расчет кривой капитала
        if not self.trades_df.empty:
            equity_curve = self.initial_capital + self.trades_df['pnl'].cumsum()
            equity_curve.plot(ax=ax, label='Strategy Equity Curve', color='blue', lw=2)

        # Расчет Buy & Hold кривой
        if not self.historical_data.empty:
            entry_price = self.historical_data['open'].iloc[0]
            quantity = self.initial_capital / entry_price
            benchmark_equity = self.historical_data['close'] * quantity

            benchmark_resampled = benchmark_equity.reset_index(drop=True)
            num_trades = len(self.trades_df)
            if num_trades > 1:
                benchmark_resampled.index = np.linspace(0, num_trades - 1, len(benchmark_resampled))
                benchmark_resampled.plot(ax=ax, label='Buy & Hold Benchmark', color='gray', linestyle='--', lw=1.5)

        ax.set_title(f"Результаты бэктеста: {report_filename}", fontsize=16)
        ax.set_xlabel("Количество сделок")
        ax.set_ylabel("Капитал")
        ax.legend()

        report_text = "\n".join([f"{key}: {value}" for key, value in display_metrics.items()])
        full_report_text = report_text + wfo_text_block # Объединяем основной текст и WFO-блок

        ax.text(0.02, 0.98, full_report_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                fontfamily='monospace')

        full_path = os.path.join(self.report_dir, f"{report_filename}.png")
        plt.savefig(full_path)
        plt.close(fig)

        console = Console()
        table = Table(title=f"Отчет о производительности: {report_filename}", show_header=True,
                      header_style="bold magenta")
        table.add_column("Метрика", style="dim", width=25)
        table.add_column("Значение", justify="right")

        for key, value in display_metrics.items():
            if "---" in key:
                table.add_section()
            else:
                table.add_row(key, str(value))

        console.print(table)
        logger.info(f"Графический отчет сохранен в файл: {full_path}")