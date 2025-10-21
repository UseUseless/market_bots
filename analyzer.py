import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os

class BacktestAnalyzer:
    def __init__(self, trades_df: pd.DataFrame, initial_capital: float, report_dir: str = "reports"):
        if trades_df.empty:
            raise ValueError("DataFrame со сделками не может быть пустым.")
            
        self.trades = trades_df
        self.initial_capital = initial_capital
        self.report_dir = report_dir
        os.makedirs(self.report_dir, exist_ok=True)
        
        # Рассчитываем кривую капитала
        self.trades['cumulative_pnl'] = self.trades['pnl'].cumsum()
        self.trades['equity_curve'] = self.initial_capital + self.trades['cumulative_pnl']

    def calculate_metrics(self) -> dict:
        """Рассчитывает ключевые метрики производительности."""
        total_pnl = self.trades['equity_curve'].iloc[-1] - self.initial_capital
        win_rate = (self.trades['pnl'] > 0).mean() * 100
        
        # Расчет максимальной просадки
        high_water_mark = self.trades['equity_curve'].cummax()
        drawdown = (self.trades['equity_curve'] - high_water_mark) / high_water_mark
        max_drawdown = abs(drawdown.min()) * 100
        
        # Расчет Profit Factor
        gross_profit = self.trades[self.trades['pnl'] > 0]['pnl'].sum()
        gross_loss = abs(self.trades[self.trades['pnl'] < 0]['pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')
        
        # Упрощенный расчет Sharpe Ratio (для простоты без учета безрисковой ставки)
        daily_returns = self.trades['equity_curve'].pct_change().dropna()
        sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252) if daily_returns.std() != 0 else 0

        return {
            "Total PnL": f"{total_pnl:.2f} ({total_pnl/self.initial_capital*100:.2f}%)",
            "Win Rate": f"{win_rate:.2f}%",
            "Max Drawdown": f"{max_drawdown:.2f}%",
            "Profit Factor": f"{profit_factor:.2f}",
            "Sharpe Ratio": f"{sharpe_ratio:.2f}",
            "Total Trades": len(self.trades)
        }

    def generate_report(self, report_filename: str):
        """Создает и сохраняет отчет с графиком и метриками."""
        metrics = self.calculate_metrics()
        
        # Создание графика
        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(15, 7))
        
        self.trades['equity_curve'].plot(ax=ax, label='Equity Curve', color='blue')
        
        ax.set_title(f"Результаты бэктеста: {report_filename}", fontsize=16)
        ax.set_xlabel("Количество сделок")
        ax.set_ylabel("Капитал")
        ax.legend()
        
        # Добавление текста с метриками на график
        report_text = "\n".join([f"{key}: {value}" for key, value in metrics.items()])
        ax.text(0.02, 0.98, report_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        # Сохранение отчета в файл
        full_path = os.path.join(self.report_dir, f"{report_filename}.png")
        plt.savefig(full_path)
        plt.close(fig) # Закрываем фигуру, чтобы не отображать ее в jupyter/etc.
        
        print(f"\n--- Отчет о производительности сохранен в файл: {full_path} ---")
        for key, value in metrics.items():
            print(f"{key:<15}: {value}")
        print("-----------------------------------------------------------------")