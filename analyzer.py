import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import logging
from rich.console import Console
from rich.table import Table
from typing import Dict, Any, Optional

from config import PATH_CONFIG, EXCHANGE_SPECIFIC_CONFIG
from optimization.metrics import MetricsCalculator, METRIC_CONFIG

logger = logging.getLogger('backtester')

class BacktestAnalyzer:
    """
    Анализирует результаты бэктеста.
    Отвечает за расчет метрики "Buy & Hold", агрегацию всех метрик
    и генерацию графического/консольного отчета.
    Делегирует расчет метрик по сделкам классу MetricsCalculator.
    """

    def __init__(self, trades_df: pd.DataFrame, historical_data: pd.DataFrame,
                 initial_capital: float, interval: str, risk_manager_type: str,
                 report_dir: str = PATH_CONFIG["REPORTS_DIR"],
                 exchange: str = 'tinkoff'):

        if trades_df.empty:
            raise ValueError("DataFrame со сделками не может быть пустым.")

        self.historical_data = historical_data
        self.initial_capital = initial_capital
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.report_dir = report_dir
        self.exchange = exchange
        os.makedirs(self.report_dir, exist_ok=True)

        exchange_config = EXCHANGE_SPECIFIC_CONFIG[self.exchange]
        annualization_factor = exchange_config["SHARPE_ANNUALIZATION_FACTOR"]
        self.calculator = MetricsCalculator(trades_df, initial_capital, annualization_factor)

        self.benchmark_equity = self._calculate_buy_and_hold()

    def _calculate_buy_and_hold(self) -> pd.Series:
        """
        Рассчитывает кривую капитала для стратегии "Купи и держи".
        """
        # Цена покупки - цена открытия самой первой свечи в истории
        entry_price = self.historical_data['open'].iloc[0]

        # Рассчитываем, сколько акций мы могли бы купить на начальный капитал
        quantity = self.initial_capital / entry_price

        # Создаем новую колонку, где стоимость портфеля пересчитывается на каждой свече
        # по цене закрытия (close)
        equity_curve = self.historical_data['close'] * quantity
        return equity_curve

    def calculate_metrics(self) -> dict:
        if not self.calculator.is_valid:
            logger.warning("Калькулятор метрик считает данные невалидными. Отчет будет содержать нули.")
            raw_metrics = {
                'pnl': 0.0, 'win_rate': 0.0, 'max_drawdown': 0.0,
                'profit_factor': 0.0, 'sharpe_ratio': 0.0, 'total_trades': 0
            }
        else:
            raw_metrics = {
                'pnl': self.calculator.calculate('pnl'),
                'win_rate': self.calculator.calculate('win_rate'),
                'max_drawdown': self.calculator.calculate('max_drawdown'),
                'profit_factor': self.calculator.calculate('profit_factor'),
                'sharpe_ratio': self.calculator.calculate('sharpe_ratio'),
                'total_trades': len(self.calculator.trades)
            }

        benchmark_pnl = self.benchmark_equity.iloc[-1] - self.initial_capital
        benchmark_pnl_percent = (benchmark_pnl / self.initial_capital) * 100

        pnl_percent = (raw_metrics['pnl'] / self.initial_capital) * 100
        profit_factor_str = f"{raw_metrics['profit_factor']:.2f}" if np.isfinite(
            raw_metrics['profit_factor']) else "inf"

        metrics = {
            "Interval": self.interval,
            "Risk Manager Type": self.risk_manager_type,
            "---": "---",
            "Total PnL (Strategy)": f"{raw_metrics['pnl']:.2f} ({pnl_percent:.2f}%)",
            "Total PnL (Buy & Hold)": f"{benchmark_pnl:.2f} ({benchmark_pnl_percent:.2f}%)",
            "--- ": "--- ",
            "Win Rate": f"{raw_metrics['win_rate'] * 100:.2f}%",
            "Max Drawdown": f"{raw_metrics['max_drawdown'] * 100:.2f}%",
            "Profit Factor": profit_factor_str,
            "Sharpe Ratio": f"{raw_metrics['sharpe_ratio']:.2f} (ann. by {self.calculator.annualization_factor}d)",
            "Total Trades": raw_metrics['total_trades']
        }
        return metrics

    def generate_report(self, report_filename: str,
                        target_metric: Optional[str] = None,
                        wfo_results: Optional[Dict[str, float]] = None):
        """
        Создает и сохраняет отчет с графиком и метриками.
        Может принимать дополнительный контекст от WFO для отображения.
        """
        # Сначала получаем базовые метрики, как и раньше
        metrics = self.calculate_metrics()

        wfo_text_block = ""
        if target_metric and wfo_results:
            # Получаем красивое имя целевой метрики
            target_metric_name = METRIC_CONFIG.get(target_metric, {}).get('name', target_metric)

            # Собираем строки для всех OOS-метрик
            oos_metrics_lines = []
            for key, value in wfo_results.items():
                metric_name = METRIC_CONFIG.get(key, {}).get('name', key)
                # Выделяем целевую метрику
                prefix = ">> " if key == target_metric else "   "
                oos_metrics_lines.append(f"{prefix}{metric_name:<20}: {value:.3f}")

            # Объединяем все в один блок
            wfo_text_block = (
                    f"\n--- WFO Final OOS Results ---\n"
                    f"Target Metric: {target_metric_name}\n"
                    f"---------------------------------\n"
                    + "\n".join(oos_metrics_lines)
            )

        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(15, 7))

        equity_curve = self.calculator.trades['equity_curve']
        equity_curve.plot(ax=ax, label='Strategy Equity Curve', color='blue', lw=2)

        benchmark_resampled = self.benchmark_equity.reset_index(drop=True)
        num_trades = len(self.calculator.trades)
        if num_trades > 1:
            benchmark_resampled.index = np.linspace(0, num_trades - 1, len(benchmark_resampled))
            benchmark_resampled.plot(ax=ax, label='Buy & Hold Benchmark', color='gray', linestyle='--', lw=1.5)

        ax.set_title(f"Результаты бэктеста: {report_filename}", fontsize=16)
        ax.set_xlabel("Количество сделок")
        ax.set_ylabel("Капитал")
        ax.legend()

        report_text = "\n".join([f"{key}: {value}" for key, value in metrics.items()])
        full_report_text = report_text + wfo_text_block  # Просто конкатенируем строки

        ax.text(0.02, 0.98, full_report_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                fontfamily='monospace')

        full_path = os.path.join(self.report_dir, f"{report_filename}.png")
        plt.savefig(full_path)
        plt.close(fig)

        # Вывод в консоль остается без изменений, так как он показывает
        # только финальные OOS-метрики, которые уже есть в `metrics`.
        console = Console()
        table = Table(title=f"Отчет о производительности: {report_filename}", show_header=True,
                      header_style="bold magenta")
        table.add_column("Метрика", style="dim", width=25)
        table.add_column("Значение", justify="right")

        for key, value in metrics.items():
            if "---" in key:
                table.add_section()
            else:
                table.add_row(key, str(value))

        console.print(table)
        logger.info(f"Графический отчет сохранен в файл: {full_path}")


class BatchTestAnalyzer:
    def __init__(self, results_df: pd.DataFrame, strategy_name: str, interval: str, risk_manager_type: str):
        if not isinstance(results_df, pd.DataFrame):
            raise TypeError("results_df должен быть pandas DataFrame.")

        self.results_df = results_df
        self.strategy_name = strategy_name
        self.interval = interval
        self.risk_manager_type = risk_manager_type

    def generate_summary_report(self):
        from rich.console import Console
        from rich.table import Table

        if self.results_df.empty:
            logging.warning("DataFrame с результатами пуст. Отчет не будет сгенерирован.")
            return

        console = Console()
        table = Table(
            title=f"Результаты пакетного тестирования: {self.strategy_name} ({self.interval}, RM: {self.risk_manager_type})")
        table.add_column("Инструмент", style="cyan", no_wrap=True)
        table.add_column("PnL (Стратегия), %", justify="right")
        table.add_column("PnL (B&H), %", justify="right")
        table.add_column("Кол-во сделок", justify="right")

        for _, row in self.results_df.sort_values("pnl_percent", ascending=False).iterrows():
            pnl_style = "green" if row['pnl_percent'] > 0 else "red"
            bh_pnl_val = row.get('bh_pnl_percent', float('nan'))
            if pd.isna(bh_pnl_val):
                bh_pnl_str = "[dim]N/A[/dim]"
            else:
                bh_pnl_style = "green" if bh_pnl_val > 0 else "red"
                bh_pnl_str = f"[{bh_pnl_style}]{bh_pnl_val:.2f}[/{bh_pnl_style}]"
            table.add_row(
                row['instrument'],
                f"[{pnl_style}]{row['pnl_percent']:.2f}[/{pnl_style}]",
                bh_pnl_str,
                str(int(row['total_trades']))
            )

        console.print(table)

        avg_pnl = self.results_df['pnl_percent'].mean()
        avg_bh_pnl = self.results_df['bh_pnl_percent'].mean()
        win_instruments_rate = (self.results_df['pnl_percent'] > 0).mean() * 100
        strategy_beats_bh_rate = (self.results_df['pnl_percent'] > self.results_df['bh_pnl_percent']).mean() * 100
        total_trades_sum = self.results_df['total_trades'].sum()

        console.print("\n--- Общая статистика по портфелю ---")
        console.print(
            f"Средний PnL (Стратегия): [bold {'green' if avg_pnl > 0 else 'red'}]{avg_pnl:.2f}%[/bold {'green' if avg_pnl > 0 else 'red'}]")
        console.print(
            f"Средний PnL (Buy & Hold): [bold {'green' if avg_bh_pnl > 0 else 'red'}]{avg_bh_pnl:.2f}%[/bold {'green' if avg_bh_pnl > 0 else 'red'}]")
        console.print(f"Доля прибыльных инструментов: [bold yellow]{win_instruments_rate:.2f}%[/bold yellow]")
        console.print(
            f"Стратегия лучше 'Buy & Hold' (% инстр.): [bold magenta]{strategy_beats_bh_rate:.2f}%[/bold magenta]")
        console.print(f"Всего сделок по всем инструментам: [bold cyan]{total_trades_sum}[/bold cyan]")