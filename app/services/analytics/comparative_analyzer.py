import pandas as pd
from typing import List, Dict, Tuple, Optional

from app.services.accounting.io import load_trades_from_file
from config import PATH_CONFIG, BACKTEST_CONFIG, EXCHANGE_SPECIFIC_CONFIG
from .metrics.portfolio_metrics import PortfolioMetricsCalculator


class ComparativeAnalyzer:
    """
    Класс для проведения сравнительного анализа результатов бэктестов.
    Отвечает за расчеты и агрегацию данных. Не содержит логики визуализации.
    """

    def __init__(self, all_backtests_summary_df: pd.DataFrame):
        """
        Инициализирует анализатор.

        :param all_backtests_summary_df: DataFrame со сводной информацией обо всех бэктестах.
        """
        if all_backtests_summary_df.empty:
            raise ValueError("Сводная таблица бэктестов не может быть пустой.")

        self.summary_df = all_backtests_summary_df.copy()
        self.logs_dir = PATH_CONFIG["LOGS_BACKTEST_DIR"]
        self.initial_capital_per_instrument = BACKTEST_CONFIG["INITIAL_CAPITAL"]
        # Делаем допущение, что большинство сравнений будет для одного типа рынка.
        # В будущем можно сделать это поле более динамическим.
        self.annualization_factor = EXCHANGE_SPECIFIC_CONFIG.get("tinkoff", {}).get("SHARPE_ANNUALIZATION_FACTOR", 252)

    # <<< ИЗМЕНЕНИЕ 3: Полностью переписанный метод. Теперь он - тонкая обертка.
    def _calculate_portfolio_metrics(self, portfolio_trades_df: pd.DataFrame,
                                     total_initial_capital: float) -> pd.Series:
        """
        Приватный helper-метод для расчета агрегированных метрик по портфелю сделок.
        Делегирует всю работу PortfolioMetricsCalculator.
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

        # Собираем только те метрики, которые нам нужны для сводной таблицы
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
        Сравнивает несколько стратегий на одном инструменте.
        (Логика этого метода остается прежней, т.к. он берет данные из summary)
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
                    equity_curve = self.initial_capital_per_instrument + trades_df['pnl'].cumsum()
                    equity_curves[row['Strategy']] = equity_curve
            except Exception as e:
                print(f"Ошибка при обработке файла для кривой капитала {row['File']}: {e}")

        metrics_df = filtered_summary.set_index('Strategy')[
            ['PnL (Strategy %)', 'Win Rate (%)', 'Max Drawdown (%)', 'Profit Factor', 'Total Trades']]
        metrics_df.rename(columns={'PnL (Strategy %)': 'PnL, %', 'Win Rate (%)': 'Win Rate, %',
                                   'Max Drawdown (%)': 'Max Drawdown, %'}, inplace=True)

        return metrics_df, equity_curves

    def analyze_instrument_robustness(
            self,
            strategy_name: str,
            instruments: List[str],
            interval: str,
            risk_manager: str
    ) -> Tuple[pd.DataFrame, Optional[pd.Series]]:
        """
        Анализирует устойчивость одной стратегии на множестве инструментов.
        (Метод обновлен для использования нового калькулятора)
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

        all_trades_df = pd.concat(all_trades_list, ignore_index=True)
        all_trades_df['exit_timestamp_utc'] = pd.to_datetime(all_trades_df['exit_timestamp_utc'])
        all_trades_df.sort_values(by='exit_timestamp_utc', inplace=True)
        all_trades_df.reset_index(drop=True, inplace=True)

        num_instruments = len(filtered_summary)
        portfolio_initial_capital = self.initial_capital_per_instrument * num_instruments

        portfolio_summary_series = self._calculate_portfolio_metrics(all_trades_df, portfolio_initial_capital)
        portfolio_summary_series.name = 'ИТОГО (портфель)'

        individual_metrics = filtered_summary[
            ['Instrument', 'PnL (Strategy %)', 'Win Rate (%)', 'Max Drawdown (%)', 'Profit Factor',
             'Total Trades']].copy()
        individual_metrics.rename(columns={'PnL (Strategy %)': 'PnL, %', 'Win Rate (%)': 'Win Rate, %',
                                           'Max Drawdown (%)': 'Max Drawdown, %'}, inplace=True)
        individual_metrics.set_index('Instrument', inplace=True)

        final_metrics_df = pd.concat([individual_metrics, portfolio_summary_series.to_frame().T])

        portfolio_equity_curve = portfolio_initial_capital + all_trades_df['pnl'].cumsum()

        return final_metrics_df, portfolio_equity_curve

    def compare_aggregated_strategies(
            self,
            strategy_names: List[str],
            instruments: List[str],
            interval: str,
            risk_manager: str
    ) -> Tuple[pd.DataFrame, Dict[str, pd.Series]]:
        """
        Сравнивает агрегированные (портфельные) результаты нескольких стратегий.
        (Этот метод автоматически начинает работать правильно после рефакторинга
         analyze_instrument_robustness, поэтому здесь изменений нет)
        """
        aggregated_metrics_list = []
        equity_curves = {}

        for strategy_name in strategy_names:
            try:
                metrics_df, equity_curve = self.analyze_instrument_robustness(
                    strategy_name=strategy_name,
                    instruments=instruments,
                    interval=interval,
                    risk_manager=risk_manager
                )
                if metrics_df.empty or equity_curve is None:
                    continue

                portfolio_summary = metrics_df.loc['ИТОГО (портфель)'].copy()
                portfolio_summary.name = strategy_name
                aggregated_metrics_list.append(portfolio_summary)
                equity_curves[strategy_name] = equity_curve

            except Exception as e:
                print(f"Ошибка при агрегации результатов для стратегии '{strategy_name}': {e}")

        if not aggregated_metrics_list:
            return pd.DataFrame(), {}

        final_metrics_df = pd.DataFrame(aggregated_metrics_list)
        final_metrics_df.index.name = "Стратегия (портфель)"

        return final_metrics_df, equity_curves

    def compare_two_portfolios(self, portfolio_a_params: Dict, portfolio_b_params: Dict) -> Tuple[
        pd.DataFrame, Dict[str, pd.Series]]:
        """
        Анализирует и сравнивает два заданных портфеля.
        Возвращает DataFrame с метриками и словарь с кривыми капитала.
        """
        metrics_list = []
        equity_curves = {}

        # Анализируем портфель А
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

        # Анализируем портфель B
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