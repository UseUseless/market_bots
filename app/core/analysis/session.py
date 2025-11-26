import pandas as pd
from typing import Dict, Any, Optional

from app.core.analysis.metrics import PortfolioMetricsCalculator, BenchmarkMetricsCalculator
from app.core.analysis.reports.plot_report import PlotReportGenerator
from app.core.analysis.reports.console_report import ConsoleReportGenerator
from app.shared.config import config

EXCHANGE_SPECIFIC_CONFIG = config.EXCHANGE_SPECIFIC_CONFIG

class AnalysisSession:
    """
    Оркестратор для полного цикла анализа одного бэктеста.
    1. Рассчитывает все необходимые метрики для портфеля и бенчмарка.
    2. Генерирует все необходимые отчеты (графические, консольные и т.д.).
    """

    def __init__(self,
                 trades_df: pd.DataFrame,
                 historical_data: pd.DataFrame,
                 initial_capital: float,
                 exchange: str,
                 interval: str,
                 risk_manager_type: str,
                 strategy_name: str):
        """
        Инициализирует сессию анализа, сразу же производя все необходимые расчеты.

        :param trades_df: DataFrame с закрытыми сделками.
        :param historical_data: DataFrame с историческими данными (OHLCV).
        :param initial_capital: Начальный капитал.
        :param exchange: Название биржи.
        :param interval: Таймфрейм.
        :param risk_manager_type: Тип используемого риск-менеджера.
        :param strategy_name: Имя стратегии.
        """
        self.trades_df = trades_df
        self.historical_data = historical_data
        self.initial_capital = initial_capital

        # Сохраняем метаданные для передачи в отчеты
        self.metadata = {
            "exchange": exchange,
            "interval": interval,
            "risk_manager_type": risk_manager_type,
            "strategy_name": strategy_name
        }

        # --- Шаг 1: Расчет метрик ---
        annual_factor = EXCHANGE_SPECIFIC_CONFIG.get(exchange, {}).get("SHARPE_ANNUALIZATION_FACTOR", 252)

        # 1.1 Рассчитываем метрики по сделкам нашей стратегии
        portfolio_calc = PortfolioMetricsCalculator(trades_df, initial_capital, annual_factor)
        self.portfolio_metrics: Dict[str, Any] = portfolio_calc.calculate_all()

        # 1.2 Рассчитываем метрики для бенчмарка (Buy & Hold)
        benchmark_calc = BenchmarkMetricsCalculator(historical_data, initial_capital, annual_factor)
        self.benchmark_metrics: Dict[str, Any] = benchmark_calc.calculate_all()

        # --- FIX START: Привязка кривых капитала к ВРЕМЕНИ (Datetime), а не к номеру строки ---

        # 1. Исправляем кривую стратегии
        if portfolio_calc.is_valid:
            # Берем таблицу сделок из калькулятора
            temp_trades = portfolio_calc.trades.copy()
            # Убеждаемся, что время выхода - это datetime
            temp_trades['exit_timestamp_utc'] = pd.to_datetime(temp_trades['exit_timestamp_utc'])
            # Устанавливаем время как индекс. Теперь график будет строиться по датам.
            self.portfolio_equity_curve = temp_trades.set_index('exit_timestamp_utc')['equity_curve']
        else:
            self.portfolio_equity_curve = pd.Series()

        # 2. Исправляем кривую бенчмарка
        if benchmark_calc.is_valid:
            # Берем рассчитанную кривую
            temp_bench = benchmark_calc.equity_curve.copy()
            # У бенчмарка индекс сейчас 0, 1, 2... (так как historical_data был сброшен)
            # Нам нужно взять колонку 'time' из данных бенчмарка и сделать её индексом
            temp_bench.index = pd.to_datetime(benchmark_calc.data['time'])
            self.benchmark_equity_curve = temp_bench
        else:
            self.benchmark_equity_curve = pd.Series()

    def generate_all_reports(self,
                             base_filename: str,
                             report_dir: str,
                             wfo_results: Optional[Dict[str, float]] = None,
                             console_output: bool = True):
        """
        Генерирует и сохраняет все доступные отчеты на основе рассчитанных метрик.

        :param base_filename: Базовое имя файла для отчетов (без расширения).
        :param report_dir: Директория для сохранения отчетов.
        :param wfo_results: Опциональный словарь с OOS-результатами WFO для отображения.
        :param console_output: Флаг, управляющий выводом отчета в консоль.
        """

        # --- Генерация графического отчета (.png) ---
        plot_gen = PlotReportGenerator(
            portfolio_metrics=self.portfolio_metrics,
            benchmark_metrics=self.benchmark_metrics,
            portfolio_equity_curve=self.portfolio_equity_curve,
            benchmark_equity_curve=self.benchmark_equity_curve,
            initial_capital=self.initial_capital,
            report_filename=base_filename,
            report_dir=report_dir,
            metadata=self.metadata
        )
        plot_gen.generate(wfo_results=wfo_results)

        # --- Генерация консольного отчета ---
        if console_output:
            console_gen = ConsoleReportGenerator(
                portfolio_metrics=self.portfolio_metrics,
                benchmark_metrics=self.benchmark_metrics,
                metadata=self.metadata,
                report_filename=base_filename
            )
            console_gen.generate()