import numpy as np
from typing import Dict, Any
from rich.console import Console
from rich.table import Table

class ConsoleReportGenerator:
    """
    Формирует и выводит в консоль сводную таблицу с результатами
    одного бэктеста, используя библиотеку Rich.
    """

    def __init__(self,
                 portfolio_metrics: Dict[str, Any],
                 benchmark_metrics: Dict[str, Any],
                 metadata: Dict[str, str],
                 report_filename: str):

        self.portfolio_metrics = portfolio_metrics
        self.benchmark_metrics = benchmark_metrics
        self.metadata = metadata
        self.report_filename = report_filename

    def _format_metrics_for_display(self) -> Dict[str, str]:
        """Форматирует числовые метрики в строки для вывода в таблицу."""
        pnl_abs = self.portfolio_metrics.get('pnl_abs', 0)
        pnl_pct = self.portfolio_metrics.get('pnl_pct', 0)
        pnl_bh_abs = self.benchmark_metrics.get('pnl_abs', 0)
        pnl_bh_pct = self.benchmark_metrics.get('pnl_pct', 0)
        profit_factor = self.portfolio_metrics.get('profit_factor', 0)

        profit_factor_str = f"{profit_factor:.2f}" if np.isfinite(profit_factor) else "inf"

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
            "Sharpe Ratio": f"{self.portfolio_metrics.get('sharpe_ratio', 0):.2f}",
            "Total Trades": str(int(self.portfolio_metrics.get('total_trades', 0)))
        }

    def generate(self):
        """Создает и выводит таблицу в консоль."""
        console = Console()
        display_metrics = self._format_metrics_for_display()

        table = Table(title=f"Performance Report: {self.report_filename}", show_header=True,
                      header_style="bold magenta")
        table.add_column("Метрика", style="dim", width=25)
        table.add_column("Значение", justify="right")

        for key, value in display_metrics.items():
            if "---" in key:
                table.add_section()
            else:
                table.add_row(key, value)

        console.print(table)