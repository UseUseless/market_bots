import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import logging
from rich.console import Console
from rich.table import Table

from config import PATH_CONFIG

class BacktestAnalyzer:
    """
    Анализирует результаты бэктеста на основе DataFrame с закрытыми сделками.
    Рассчитывает ключевые метрики и генерирует графический отчет.
    """

    def __init__(self, trades_df: pd.DataFrame, historical_data: pd.DataFrame,
                 initial_capital: float, interval: str, risk_manager_type: str,
                 report_dir: str = PATH_CONFIG["REPORTS_DIR"]):
        # Проверяем, что нам вообще передали какие-то данные
        if trades_df.empty:
            raise ValueError("DataFrame со сделками не может быть пустым.")
            
        self.trades = trades_df
        self.historical_data = historical_data
        self.initial_capital = initial_capital
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.report_dir = report_dir
        os.makedirs(self.report_dir, exist_ok=True)
        
        # Считаем накопительный PnL после каждой сделки
        self.trades['cumulative_pnl'] = self.trades['pnl'].cumsum()
        # Прибавляем накопительный PnL к начальному капиталу, чтобы получить кривую роста
        self.trades['equity_curve'] = self.initial_capital + self.trades['cumulative_pnl']

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
        """Рассчитывает ключевые метрики производительности стратегии."""
        # Общий финансовый результат
        total_pnl = self.trades['equity_curve'].iloc[-1] - self.initial_capital

        # Процент прибыльных сделок (Win Rate)
        win_rate = (self.trades['pnl'] > 0).mean() * 100

        # Расчет максимальной просадки (Max Drawdown)
        # 1. Считаем "водяной знак" - максимальное значение капитала, достигнутое на данный момент.
        high_water_mark = self.trades['equity_curve'].cummax()
        # 2. Считаем просадку в каждый момент времени как отклонение от "водяного знака".
        drawdown = (self.trades['equity_curve'] - high_water_mark) / high_water_mark
        # 3. Находим максимальное значение этой просадки.
        max_drawdown = abs(drawdown.min()) * 100

        # Расчет Profit Factor
        # Сумма всех прибылей
        gross_profit = self.trades[self.trades['pnl'] > 0]['pnl'].sum()
        # Сумма всех убытков (по модулю)
        gross_loss = abs(self.trades[self.trades['pnl'] < 0]['pnl'].sum())
        # Отношение прибылей к убыткам. Обрабатываем случай деления на ноль.
        profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')

        # Упрощенный расчет Sharpe Ratio (без учета безрисковой ставки)
        # 1. Считаем доходность от сделки к сделке.
        daily_returns = self.trades['equity_curve'].pct_change().dropna()
        # 2. Считаем по формуле: (средняя доходность / станд. отклонение доходности) * sqrt(252)
        # sqrt(252) - это годовая поправка (примерное число торговых дней в году).
        sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() != 0 else 0

        # Расчет метрик для "Buy and Hold
        benchmark_pnl = self.benchmark_equity.iloc[-1] - self.initial_capital
        benchmark_pnl_percent = (benchmark_pnl / self.initial_capital) * 100

        metrics = {
            # --- Параметры запуска ---
            "Interval": self.interval,
            "Risk Manager Type": self.risk_manager_type,
            "---": "---", # Разделитель
            "Total PnL (Strategy)": f"{total_pnl:.2f} ({total_pnl / self.initial_capital * 100:.2f}%)",
            "Total PnL (Buy & Hold)": f"{benchmark_pnl:.2f} ({benchmark_pnl_percent:.2f}%)", # Новая метрика
            "--- ": "--- ", # Разделитель
            "Win Rate": f"{win_rate:.2f}%",
            "Max Drawdown": f"{max_drawdown:.2f}%",
            "Profit Factor": f"{profit_factor:.2f}",
            "Sharpe Ratio": f"{sharpe_ratio:.2f}",
            "Total Trades": len(self.trades)
        }

        # Возвращаем все метрики
        return metrics

    def generate_report(self, report_filename: str):
        """Создает и сохраняет отчет с графиком и метриками."""
        metrics = self.calculate_metrics()

        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(15, 7))
        
        # Создание графиков
        # График нашей стратегии. Важно: используем индекс сделок.
        self.trades['equity_curve'].plot(ax=ax, label='Strategy Equity Curve', color='blue', lw=2)

        # График "Buy and Hold". Важно: нужно привести его индекс к тому же масштабу,
        # что и у графика сделок, чтобы они корректно наложились.
        # Мы "растягиваем" индекс от 0 до N-1 сделок на всю длину исторических данных.
        benchmark_resampled = self.benchmark_equity.reset_index(drop=True)
        benchmark_resampled.index = np.linspace(0, len(self.trades) - 1, len(benchmark_resampled))
        benchmark_resampled.plot(ax=ax, label='Buy & Hold Benchmark', color='gray', linestyle='--', lw=1.5)

        ax.set_title(f"Результаты бэктеста: {report_filename}", fontsize=16)
        ax.set_xlabel("Количество сделок")
        ax.set_ylabel("Капитал")
        ax.legend()
        
        # Формируем одну строку из словаря с метриками
        report_text = "\n".join([f"{key}: {value}" for key, value in metrics.items()])
        # Размещаем этот текст в левом верхнем углу графика
        ax.text(0.02, 0.98, report_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                fontfamily='monospace')

        # Сохранение отчета в файл
        full_path = os.path.join(self.report_dir, f"{report_filename}.png")
        plt.savefig(full_path)
        plt.close(fig) # Очищаем график из памяти

        # --- Вывод метрик в консоль ---
        console = Console()
        table = Table(title=f"Отчет о производительности: {report_filename}", show_header=True,
                      header_style="bold magenta")
        table.add_column("Метрика", style="dim", width=20)
        table.add_column("Значение", justify="right")

        for key, value in metrics.items():
            if key == "---":
                table.add_section()
            else:
                table.add_row(key, str(value))

        console.print(table)
        logging.info(f"Графический отчет сохранен в файл: {full_path}")