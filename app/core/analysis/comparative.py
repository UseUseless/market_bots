"""
Модуль сравнительного анализа.

Предоставляет инструменты для сбора и сравнения результатов множества бэктестов.
Используется в Дашборде для:
1.  Сравнения разных стратегий на одном инструменте.
2.  Анализа устойчивости стратегии на наборе инструментов.
3.  Сравнения двух произвольных портфелей (A/B тестирование).

Этот модуль работает с уже готовыми файлами логов (`_trades.jsonl`), загружая их,
объединяя и пересчитывая метрики для объединенного потока сделок.
"""

import pandas as pd
from typing import List, Dict, Tuple, Optional

import app.infrastructure.feeds.backtest.provider
from app.infrastructure.files.file_io import load_trades_from_file
from app.shared.config import config
from app.core.analysis.metrics import PortfolioMetricsCalculator

PATH_CONFIG = config.PATH_CONFIG
BACKTEST_CONFIG = config.BACKTEST_CONFIG
EXCHANGE_SPECIFIC_CONFIG = app.infrastructure.feeds.backtest.provider.EXCHANGE_SPECIFIC_CONFIG


class ComparativeAnalyzer:
    """
    Аналитический движок для сравнения результатов.

    Хранит кеш метаданных (`summary_df`) для быстрого поиска файлов логов.

    Attributes:
        summary_df (pd.DataFrame): Сводная таблица всех доступных бэктестов.
        logs_dir (str): Путь к папке с логами.
        initial_capital_per_instrument (float): Капитал на 1 инструмент в тесте.
    """

    def __init__(self, all_backtests_summary_df: pd.DataFrame):
        """
        Инициализирует анализатор.

        Args:
            all_backtests_summary_df (pd.DataFrame): DataFrame, полученный из
                `app.adapters.dashboard.components.data_loader.load_all_backtests`.
                Содержит пути к файлам и базовые метрики.

        Raises:
            ValueError: Если сводная таблица пуста.
        """
        if all_backtests_summary_df.empty:
            raise ValueError("Сводная таблица бэктестов не может быть пустой.")

        self.summary_df = all_backtests_summary_df.copy()
        self.logs_dir = PATH_CONFIG["LOGS_BACKTEST_DIR"]
        self.initial_capital_per_instrument = BACKTEST_CONFIG["INITIAL_CAPITAL"]

        # Дефолтный коэффициент аннуализации (можно сделать динамическим)
        self.annualization_factor = EXCHANGE_SPECIFIC_CONFIG.get("tinkoff", {}).get("SHARPE_ANNUALIZATION_FACTOR", 252)

    def _calculate_portfolio_metrics(self, portfolio_trades_df: pd.DataFrame,
                                     total_initial_capital: float) -> pd.Series:
        """
        Рассчитывает метрики для набора инструментов.

        Args:
            portfolio_trades_df (pd.DataFrame): Объединенный список всех сделок.
            total_initial_capital (float): Суммарный стартовый капитал портфеля.

        Returns:
            pd.Series: Основные метрики (PnL, Sharpe, Drawdown).
        """
        if portfolio_trades_df.empty:
            return pd.Series(dtype=float)

        calculator = PortfolioMetricsCalculator(
            trades_df=portfolio_trades_df,
            initial_capital=total_initial_capital,
            annualization_factor=self.annualization_factor
        )

        if not calculator.is_valid:
            return pd.Series(dtype=float)

        all_metrics = calculator.calculate_all()

        return pd.Series({
            'PnL, %': all_metrics.get('pnl_pct', 0.0),
            'Win Rate, %': all_metrics.get('win_rate', 0.0) * 100,
            'Max Drawdown, %': all_metrics.get('max_drawdown', 0.0) * 100,
            'Profit Factor': all_metrics.get('profit_factor', 0.0),
            'Sharpe Ratio': all_metrics.get('sharpe_ratio', 0.0),
            'Total Trades': all_metrics.get('total_trades', 0)
        })

    def compare_strategies_on_instrument(
            self,
            strategy_names: List[str],
            instrument: str,
            interval: str,
            risk_manager: str
    ) -> Tuple[pd.DataFrame, Dict[str, pd.Series]]:
        """
        Сравнивает эффективность разных стратегий на одном и том же инструменте.

        Args:
            strategy_names: Список имен стратегий.
            instrument: Тикер.
            interval: Таймфрейм.
            risk_manager: Тип риск-менеджера.

        Returns:
            Tuple:
                - pd.DataFrame: Таблица с метриками каждой стратегии.
                - Dict[str, pd.Series]: Словарь кривых капитала {StrategyName: EquityCurve}.
        """
        filtered_summary = self.summary_df[
            (self.summary_df['Strategy'].isin(strategy_names)) &
            (self.summary_df['Instrument'] == instrument) &
            (self.summary_df['Interval'] == interval) &
            (self.summary_df['Risk Manager'] == risk_manager)
            ]

        if filtered_summary.empty:
            return pd.DataFrame(), {}

        equity_curves = {}
        for _, row in filtered_summary.iterrows():
            try:
                trades_df = load_trades_from_file(row['File Path'])
                if not trades_df.empty:
                    # Восстанавливаем кривую капитала из PnL сделок
                    equity_curve = self.initial_capital_per_instrument + trades_df['pnl'].cumsum()
                    equity_curves[row['Strategy']] = equity_curve
            except Exception as e:
                print(f"Ошибка при обработке файла для кривой капитала {row['File']}: {e}")

        # Формируем таблицу метрик из уже готовых данных summary_df (чтобы не пересчитывать)
        metrics_df = filtered_summary.set_index('Strategy')[
            ['PnL (Strategy %)', 'Win Rate (%)', 'Max Drawdown (%)', 'Profit Factor', 'Total Trades']]

        metrics_df.rename(columns={
            'PnL (Strategy %)': 'PnL, %',
            'Win Rate (%)': 'Win Rate, %',
            'Max Drawdown (%)': 'Max Drawdown, %'
        }, inplace=True)

        return metrics_df, equity_curves

    def analyze_instrument_robustness(
            self,
            strategy_name: str,
            instruments: List[str],
            interval: str,
            risk_manager: str
    ) -> Tuple[pd.DataFrame, Optional[pd.Series]]:
        """
        Анализирует "портфель", состоящий из одной стратегии, запущенной на множестве инструментов.
        Позволяет оценить устойчивость стратегии.

        Args:
            strategy_name: Имя стратегии.
            instruments: Список тикеров.
            interval: Таймфрейм.
            risk_manager: Риск-менеджер.

        Returns:
            Tuple:
                - pd.DataFrame: Метрики по каждому инструменту + ИТОГОВАЯ строка портфеля.
                - pd.Series: Кривая капитала всего портфеля.
        """
        filtered_summary = self.summary_df[
            (self.summary_df['Strategy'] == strategy_name) &
            (self.summary_df['Instrument'].isin(instruments)) &
            (self.summary_df['Interval'] == interval) &
            (self.summary_df['Risk Manager'] == risk_manager)
            ]

        if filtered_summary.empty:
            return pd.DataFrame(), None

        all_trades_list = []
        for _, row in filtered_summary.iterrows():
            try:
                trades_df = load_trades_from_file(row['File Path'])
                if not trades_df.empty:
                    all_trades_list.append(trades_df)
            except Exception as e:
                print(f"Ошибка при загрузке файла {row['File']}: {e}")

        if not all_trades_list:
            return pd.DataFrame(), None

        # Объединяем сделки и сортируем по времени выхода, чтобы симулировать
        # последовательность событий в портфеле.
        all_trades_df = pd.concat(all_trades_list, ignore_index=True)
        all_trades_df['exit_time'] = pd.to_datetime(all_trades_df['exit_time'])
        all_trades_df.sort_values(by='exit_time', inplace=True)
        all_trades_df.reset_index(drop=True, inplace=True)

        # Капитал портфеля = сумма капиталов отдельных стратегий
        num_instruments = len(filtered_summary)
        portfolio_initial_capital = self.initial_capital_per_instrument * num_instruments

        # Рассчитываем метрики для всего портфеля
        portfolio_summary_series = self._calculate_portfolio_metrics(all_trades_df, portfolio_initial_capital)
        portfolio_summary_series.name = 'ИТОГО (портфель)'

        # Собираем таблицу: метрики по инструментам + итоговая строка
        individual_metrics = filtered_summary[
            ['Instrument', 'PnL (Strategy %)', 'Win Rate (%)', 'Max Drawdown (%)', 'Profit Factor',
             'Total Trades']].copy()

        individual_metrics.rename(columns={
            'PnL (Strategy %)': 'PnL, %',
            'Win Rate (%)': 'Win Rate, %',
            'Max Drawdown (%)': 'Max Drawdown, %'
        }, inplace=True)

        individual_metrics.set_index('Instrument', inplace=True)

        final_metrics_df = pd.concat([individual_metrics, portfolio_summary_series.to_frame().T])

        # Кривая капитала портфеля
        portfolio_equity_curve = portfolio_initial_capital + all_trades_df['pnl'].cumsum()

        return final_metrics_df, portfolio_equity_curve

    def compare_two_portfolios(self, portfolio_a_params: Dict, portfolio_b_params: Dict) -> Tuple[
        pd.DataFrame, Dict[str, pd.Series]]:
        """
        Сравнивает два произвольных портфеля (набор стратегий/инструментов).

        Args:
            portfolio_a_params (Dict): Параметры первого портфеля.
            portfolio_b_params (Dict): Параметры второго портфеля.

        Returns:
            Tuple: Таблица сравнения метрик и словарь кривых капитала.
        """
        metrics_list = []
        equity_curves = {}

        # 1. Анализируем портфель А
        metrics_a, curve_a = self.analyze_instrument_robustness(
            strategy_name=portfolio_a_params['strategy'],
            instruments=portfolio_a_params['instruments'],
            interval=portfolio_a_params['interval'],
            risk_manager=portfolio_a_params['rm']
        )
        if not metrics_a.empty and curve_a is not None:
            summary_a = metrics_a.loc['ИТОГО (портфель)'].copy()
            summary_a.name = "Портфель A"
            metrics_list.append(summary_a)
            equity_curves["Портфель A"] = curve_a

        # 2. Анализируем портфель B
        metrics_b, curve_b = self.analyze_instrument_robustness(
            strategy_name=portfolio_b_params['strategy'],
            instruments=portfolio_b_params['instruments'],
            interval=portfolio_b_params['interval'],
            risk_manager=portfolio_b_params['rm']
        )
        if not metrics_b.empty and curve_b is not None:
            summary_b = metrics_b.loc['ИТОГО (портфель)'].copy()
            summary_b.name = "Портфель B"
            metrics_list.append(summary_b)
            equity_curves["Портфель B"] = curve_b

        if not metrics_list:
            return pd.DataFrame(), {}

        final_metrics_df = pd.DataFrame(metrics_list)
        return final_metrics_df, equity_curves