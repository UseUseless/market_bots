"""
Модуль генерации консольных отчетов.

Отвечает за визуализацию результатов одиночного бэктеста прямо в терминале.
Использует библиотеку `rich` для создания красивых, отформатированных таблиц.
Это позволяет пользователю мгновенно оценить результат теста без открытия файлов.
"""

import numpy as np
from typing import Dict, Any
from rich.console import Console
from rich.table import Table


class ConsoleReportGenerator:
    """
    Генератор текстового отчета для CLI (Command Line Interface).

    Преобразует сырые словари метрик в отформатированную таблицу с секциями.

    Attributes:
        portfolio_metrics (Dict[str, Any]): Метрики стратегии (PnL, Sharpe и т.д.).
        benchmark_metrics (Dict[str, Any]): Метрики Buy & Hold.
        metadata (Dict[str, str]): Контекст (имя стратегии, таймфрейм).
        report_filename (str): Имя файла лога (используется как заголовок таблицы).
    """

    def __init__(self,
                 portfolio_metrics: Dict[str, Any],
                 benchmark_metrics: Dict[str, Any],
                 metadata: Dict[str, str],
                 report_filename: str):
        """
        Инициализирует генератор данными для отчета.

        Args:
            portfolio_metrics (Dict): Результат работы `PortfolioMetricsCalculator`.
            benchmark_metrics (Dict): Результат работы `BenchmarkMetricsCalculator`.
            metadata (Dict): Метаданные запуска (exchange, interval, strategy).
            report_filename (str): Имя файла для заголовка.
        """
        self.portfolio_metrics = portfolio_metrics
        self.benchmark_metrics = benchmark_metrics
        self.metadata = metadata
        self.report_filename = report_filename

    def _format_metrics_for_display(self) -> Dict[str, str]:
        """
        Преобразует числовые значения метрик в читаемые строки.

        Выполняет:
        1. Округление float до 2 знаков.
        2. Добавление знаков процентов (%).
        3. Обработку бесконечных значений (например, Profit Factor).

        Returns:
            Dict[str, str]: Словарь {Название_Метрики: Отформатированное_Значение}.
                            Ключи, содержащие "---", используются как разделители секций.
        """
        pnl_abs = self.portfolio_metrics.get('pnl_abs', 0)
        pnl_pct = self.portfolio_metrics.get('pnl_pct', 0)
        pnl_bh_abs = self.benchmark_metrics.get('pnl_abs', 0)
        pnl_bh_pct = self.benchmark_metrics.get('pnl_pct', 0)
        profit_factor = self.portfolio_metrics.get('profit_factor', 0)

        # Обработка Profit Factor = inf (когда нет убыточных сделок)
        profit_factor_str = f"{profit_factor:.2f}" if np.isfinite(profit_factor) else "inf"

        return {
            "Interval": self.metadata.get("interval", "N/A"),
            "Risk Manager": self.metadata.get("risk_manager_type", "N/A"),

            # Разделитель секций
            "---": "---",

            "Total PnL (Strategy)": f"{pnl_abs:.2f} ({pnl_pct:.2f}%)",
            "Total PnL (Buy & Hold)": f"{pnl_bh_abs:.2f} ({pnl_bh_pct:.2f}%)",

            # Разделитель секций
            "--- ": "--- ",

            "Win Rate": f"{self.portfolio_metrics.get('win_rate', 0) * 100:.2f}%",
            "Max Drawdown": f"{self.portfolio_metrics.get('max_drawdown', 0) * 100:.2f}%",
            "Profit Factor": profit_factor_str,
            "Sharpe Ratio": f"{self.portfolio_metrics.get('sharpe_ratio', 0):.2f}",
            "Total Trades": str(int(self.portfolio_metrics.get('total_trades', 0)))
        }

    def generate(self):
        """
        Рендерит таблицу и выводит её в стандартный вывод (stdout).
        """
        console = Console()
        display_metrics = self._format_metrics_for_display()

        table = Table(
            title=f"Performance Report: {self.report_filename}",
            show_header=True,
            header_style="bold magenta"
        )
        table.add_column("Метрика", style="dim", width=30)
        table.add_column("Значение", justify="right", style="bold green")

        for key, value in display_metrics.items():
            if "---" in key:
                # Добавляем пустую строку-разделитель в таблицу
                table.add_section()
            else:
                table.add_row(key, value)

        console.print(table)