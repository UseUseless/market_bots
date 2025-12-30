"""
Модуль анализа результатов.

Этот класс служит оркестратором для пост-процессинга результатов бэктеста.
Он объединяет расчет математических метрик и генерацию визуальных отчетов.

После завершения симуляции (`BacktestEngine`), сырые данные (сделки и история цен)
передаются сюда. Сессия превращает их в человекочитаемые результаты:
коэффициенты Шарпа, графики доходности и консольные таблицы.
"""

import pandas as pd
from typing import Dict, Any, Optional

import app.infrastructure.feeds.backtest.provider
from app.core.analysis.metrics import PortfolioMetricsCalculator, BenchmarkMetricsCalculator
from app.core.analysis.reports.plot import PlotReportGenerator
from app.core.analysis.reports.console import ConsoleReportGenerator
from app.shared.config import config

EXCHANGE_SPECIFIC_CONFIG = app.infrastructure.feeds.backtest.provider.EXCHANGE_SPECIFIC_CONFIG


class AnalysisSession:
    """
    Контроллер аналитической сессии.

    Выполняет следующие задачи при инициализации:
    1.  Рассчитывает метрики портфеля (Portfolio Metrics).
    2.  Рассчитывает метрики бенчмарка Buy & Hold (Benchmark Metrics).
    3.  Синхронизирует кривые капитала по времени для построения графиков.

    Attributes:
        portfolio_metrics (Dict[str, Any]): Результаты расчета для стратегии.
        benchmark_metrics (Dict[str, Any]): Результаты расчета для Buy & Hold.
        portfolio_equity_curve (pd.Series): Временной ряд капитала стратегии (индекс - datetime).
        benchmark_equity_curve (pd.Series): Временной ряд капитала бенчмарка (индекс - datetime).
        metadata (Dict[str, str]): Контекстная информация (биржа, тикер, имя стратегии).
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
        Инициализирует сессию и запускает расчеты.

        Args:
            trades_df (pd.DataFrame): DataFrame со списком закрытых сделок.
            historical_data (pd.DataFrame): История свечей (OHLCV) за период теста.
            initial_capital (float): Стартовый депозит.
            exchange (str): Название биржи.
            interval (str): Рабочий таймфрейм.
            risk_manager_type (str): Имя использованного риск-менеджера.
            strategy_name (str): Имя стратегии.
        """
        self.trades_df = trades_df
        self.historical_data = historical_data
        self.initial_capital = initial_capital

        # Сохраняем метаданные для заголовков отчетов
        self.metadata = {
            "exchange": exchange,
            "interval": interval,
            "risk_manager_type": risk_manager_type,
            "strategy_name": strategy_name
        }

        # Определяем коэффициент аннуализации (например, 252 дня для акций, 365 для крипты)
        annual_factor = EXCHANGE_SPECIFIC_CONFIG.get(exchange, {}).get("SHARPE_ANNUALIZATION_FACTOR", 252)

        # 1. Расчет метрик (Portfolio & Benchmark)
        portfolio_calc = PortfolioMetricsCalculator(trades_df, initial_capital, annual_factor)
        self.portfolio_metrics: Dict[str, Any] = portfolio_calc.calculate_all()

        benchmark_calc = BenchmarkMetricsCalculator(historical_data, initial_capital, annual_factor)
        self.benchmark_metrics: Dict[str, Any] = benchmark_calc.calculate_all()

        # 2. Подготовка данных для графиков (Time Alignment)
        # Нам нужно, чтобы кривая капитала имела DatetimeIndex, а не просто номер сделки,
        # чтобы корректно наложить её на график цены бенчмарка.

        # 2.1. Кривая стратегии
        if portfolio_calc.is_valid:
            temp_trades = portfolio_calc.trades.copy()
            # Конвертируем время выхода в datetime (если оно еще не конвертировано)
            temp_trades['exit_time'] = pd.to_datetime(temp_trades['exit_time'])

            # Устанавливаем время как индекс и берем последнее значение капитала на эту дату
            self.portfolio_equity_curve = temp_trades.set_index('exit_time')['equity_curve']
            self.portfolio_equity_curve = self.portfolio_equity_curve.groupby(level=0).last()
        else:
            self.portfolio_equity_curve = pd.Series()

        # 2.2. Кривая бенчмарка
        if benchmark_calc.is_valid:
            temp_bench = benchmark_calc.equity_curve.copy()
            # У бенчмарка индекс был числовой (0..N), восстанавливаем время из historical_data
            temp_bench.index = pd.to_datetime(benchmark_calc.data['time'])
            self.benchmark_equity_curve = temp_bench.groupby(level=0).last()
        else:
            self.benchmark_equity_curve = pd.Series()

    def generate_all_reports(self,
                             base_filename: str,
                             report_dir: str,
                             wfo_results: Optional[Dict[str, float]] = None,
                             console_output: bool = True):
        """
        Создает отчеты.

        Args:
            base_filename (str): Имя файла (без расширения), которое будет использовано для отчетов.
            report_dir (str): Путь к директории для сохранения файлов.
            wfo_results (Optional[Dict]): Результаты Walk-Forward Optimization,
                                          чтобы добавить их текстом на график.
            console_output (bool): Если True, выводит краткую сводку в терминал.
        """
        # 1. Графический отчет (PNG)
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

        # 2. Консольный отчет (Rich Table)
        if console_output:
            console_gen = ConsoleReportGenerator(
                portfolio_metrics=self.portfolio_metrics,
                benchmark_metrics=self.benchmark_metrics,
                metadata=self.metadata,
                report_filename=base_filename
            )
            console_gen.generate()