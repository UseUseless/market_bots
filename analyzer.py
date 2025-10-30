import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
import logging

from config import PATH_CONFIG

class BacktestAnalyzer:
    """
    Анализирует результаты бэктеста на основе DataFrame с закрытыми сделками.
    Рассчитывает ключевые метрики и генерирует графический отчет.
    """
    def __init__(self, trades_df: pd.DataFrame, initial_capital: float, interval: str, risk_manager_type: str, report_dir: str = PATH_CONFIG["REPORTS_DIR"]):
        # Проверяем, что нам вообще передали какие-то данные
        if trades_df.empty:
            raise ValueError("DataFrame со сделками не может быть пустым.")
            
        self.trades = trades_df
        self.initial_capital = initial_capital
        self.interval = interval
        self.risk_manager_type = risk_manager_type
        self.report_dir = report_dir
        os.makedirs(self.report_dir, exist_ok=True)
        
        # Считаем накопительный PnL после каждой сделки
        self.trades['cumulative_pnl'] = self.trades['pnl'].cumsum()
        # Прибавляем накопительный PnL к начальному капиталу, чтобы получить кривую роста
        self.trades['equity_curve'] = self.initial_capital + self.trades['cumulative_pnl']

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

        metrics = {
            # --- Параметры запуска ---
            "Interval": self.interval,
            "Risk Manager Type": self.risk_manager_type,
            "---": "---",
            "Total PnL": f"{total_pnl:.2f} ({total_pnl / self.initial_capital * 100:.2f}%)",
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
        
        # Создание графика
        plt.style.use('seaborn-v0_8-darkgrid')
        fig, ax = plt.subplots(figsize=(15, 7)) # Создаем основу графика

        # Рисуем кривую капитала
        self.trades['equity_curve'].plot(ax=ax, label='Equity Curve', color='blue')

        # Настраиваем заголовок и подписи осей
        ax.set_title(f"Результаты бэктеста: {report_filename}", fontsize=16)
        ax.set_xlabel("Количество сделок")
        ax.set_ylabel("Капитал")
        ax.legend()
        
        # Формируем одну строку из словаря с метриками
        report_text = "\n".join([f"{key}: {value}" for key, value in metrics.items()])
        # Размещаем этот текст в левом верхнем углу графика
        ax.text(0.02, 0.98, report_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        # Сохранение отчета в файл
        full_path = os.path.join(self.report_dir, f"{report_filename}.png")
        plt.savefig(full_path)
        plt.close(fig) # Очищаем график из памяти

        # --- Вывод метрик в консоль ---
        logging.info(f"--- Отчет о производительности сохранен в файл: {full_path} ---")
        for key, value in metrics.items():
            logging.info(f"{key:<15}: {value}")
        logging.info("-----------------------------------------------------------------")