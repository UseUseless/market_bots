import pandas as pd
import plotly.graph_objects as go
import os
import numpy as np
from typing import List, Dict, Any, Tuple

from utils.file_io import load_trades_from_file
from analyzer import BacktestAnalyzer
from config import BACKTEST_CONFIG, PATH_CONFIG


class ComparativeAnalyzer:
    """
    Класс для проведения сравнительного анализа результатов бэктестов.
    - Сравнивает несколько стратегий на одном инструменте.
    - Сравнивает агрегированные результаты нескольких стратегий.
    """

    def __init__(self, all_backtests_summary_df: pd.DataFrame):
        """
        Инициализирует анализатор.

        :param all_backtests_summary_df: DataFrame со сводной информацией обо всех бэктестах,
                                         загруженный в dashboard.py.
        """
        if all_backtests_summary_df.empty:
            raise ValueError("Сводная таблица бэктестов не может быть пустой.")

        self.summary_df = all_backtests_summary_df.copy()
        self.logs_dir = PATH_CONFIG["LOGS_DIR"]
        self.initial_capital = BACKTEST_CONFIG["INITIAL_CAPITAL"]

    def _calculate_portfolio_metrics(self, portfolio_trades_df: pd.DataFrame, initial_capital: float) -> pd.Series:
        """
        Приватный helper-метод для расчета агрегированных метрик по портфелю сделок.
        """
        if portfolio_trades_df.empty:
            return pd.Series(dtype=float)

        # PnL
        total_pnl = portfolio_trades_df['portfolio_equity'].iloc[-1] - initial_capital
        total_pnl_percent = (total_pnl / initial_capital) * 100

        # Win Rate
        win_rate = (portfolio_trades_df['pnl'] > 0).mean() * 100

        # Max Drawdown
        high_water_mark = portfolio_trades_df['portfolio_equity'].cummax()
        drawdown = (portfolio_trades_df['portfolio_equity'] - high_water_mark) / high_water_mark
        max_drawdown = abs(drawdown.min()) * 100

        # Profit Factor
        gross_profit = portfolio_trades_df[portfolio_trades_df['pnl'] > 0]['pnl'].sum()
        gross_loss = abs(portfolio_trades_df[portfolio_trades_df['pnl'] < 0]['pnl'].sum())
        profit_factor = gross_profit / gross_loss if gross_loss != 0 else float('inf')

        # Sharpe Ratio (упрощенный, по сделкам)
        trade_returns = portfolio_trades_df['portfolio_equity'].pct_change().dropna()
        sharpe_ratio = (trade_returns.mean() / trade_returns.std()) * np.sqrt(
            len(portfolio_trades_df)) if trade_returns.std() != 0 else 0

        return pd.Series({
            'PnL, %': total_pnl_percent,
            'Win Rate, %': win_rate,
            'Max Drawdown, %': max_drawdown,
            'Profit Factor': profit_factor,
            'Sharpe Ratio': sharpe_ratio,
            'Total Trades': len(portfolio_trades_df)
        })

    def compare_strategies_on_instrument(
            self,
            strategy_names: List[str],
            instrument: str,
            interval: str,
            risk_manager: str
    ) -> Tuple[pd.DataFrame, go.Figure]:
        """
        Сравнивает несколько стратегий на одном и том же инструменте.

        :param strategy_names: Список названий стратегий для сравнения.
        :param instrument: Тикер инструмента.
        :param interval: Таймфрейм.
        :param risk_manager: Тип риск-менеджера.
        :return: Кортеж (DataFrame с метриками, Figure с графиками капитала).
        """
        # 1. Фильтруем сводную таблицу, чтобы найти нужные нам бэктесты
        filtered_summary = self.summary_df[
            (self.summary_df['Strategy'].isin(strategy_names)) &
            (self.summary_df['Instrument'] == instrument) &
            (self.summary_df['Interval'] == interval) &
            (self.summary_df['Risk Manager'] == risk_manager)
            ]

        if filtered_summary.empty:
            return pd.DataFrame(), go.Figure().update_layout(
                title_text="Не найдено бэктестов для сравнения по заданным фильтрам")

        all_metrics = []
        fig = go.Figure()

        # Загружаем исторические данные ОДИН раз, т.к. инструмент один и тот же
        # Берем биржу из первой найденной строки
        exchange = filtered_summary.iloc[0]['Exchange']
        data_path = os.path.join(PATH_CONFIG["DATA_DIR"], exchange, interval, f"{instrument}.parquet")
        try:
            historical_data = pd.read_parquet(data_path)
        except FileNotFoundError:
            return pd.DataFrame(), go.Figure().update_layout(
                title_text=f"Файл с историческими данными не найден: {data_path}")

        # 2. Проходим по каждому найденному бэктесту
        for _, row in filtered_summary.iterrows():
            try:
                # Загружаем сделки для этого бэктеста
                trades_df = load_trades_from_file(os.path.join(self.logs_dir, row['File']))
                if trades_df.empty:
                    continue

                # 3. Используем наш старый добрый BacktestAnalyzer для расчетов
                analyzer = BacktestAnalyzer(
                    trades_df=trades_df,
                    historical_data=historical_data,
                    initial_capital=self.initial_capital,
                    interval=interval,
                    risk_manager_type=risk_manager
                )

                # Получаем метрики и добавляем имя стратегии для таблицы
                metrics = analyzer.calculate_metrics()
                metrics_dict = {
                    "Стратегия": row['Strategy'],
                    "PnL, %": float(metrics["Total PnL (Strategy)"].split(' ')[1].replace('(', '').replace('%)', '')),
                    "Win Rate, %": float(metrics["Win Rate"].replace('%', '')),
                    "Max Drawdown, %": float(metrics["Max Drawdown"].replace('%', '')),
                    "Profit Factor": float(metrics["Profit Factor"]),
                    "Total Trades": int(metrics["Total Trades"]),
                }
                all_metrics.append(metrics_dict)

                # 4. Добавляем кривую капитала на общий график
                fig.add_trace(go.Scatter(
                    x=analyzer.trades.index,
                    y=analyzer.trades['equity_curve'],
                    mode='lines',
                    name=row['Strategy']
                ))

            except Exception as e:
                print(f"Ошибка при обработке файла {row['File']}: {e}")

        if not all_metrics:
            return pd.DataFrame(), go.Figure().update_layout(
                title_text="Не удалось обработать ни одного файла для сравнения")

        # 5. Собираем итоговую таблицу и настраиваем график
        metrics_df = pd.DataFrame(all_metrics).set_index("Стратегия")

        fig.update_layout(
            title_text=f"Сравнение стратегий на {instrument} ({interval}, RM: {risk_manager})",
            xaxis_title="Количество сделок",
            yaxis_title="Капитал",
            legend_title_text="Стратегии"
        )

        return metrics_df, fig

    def analyze_instrument_robustness(
            self,
            strategy_name: str,
            instruments: List[str],
            interval: str,
            risk_manager: str
    ) -> Tuple[pd.DataFrame, go.Figure]:
        """
        Анализирует робастность (устойчивость) одной стратегии на множестве инструментов.
        Строит единую портфельную кривую капитала и рассчитывает агрегированные метрики..

        :param strategy_name: Название стратегии.
        :param instruments: Список тикеров инструментов.
        :param interval: Таймфрейм.
        :param risk_manager: Тип риск-менеджера.
        :return: Кортеж (DataFrame с метриками по каждому инструменту + ИТОГО, Figure с портфельным графиком).
        """
        # 1. Фильтруем сводную таблицу, чтобы найти все нужные бэктесты
        filtered_summary = self.summary_df[
            (self.summary_df['Strategy'] == strategy_name) &
            (self.summary_df['Instrument'].isin(instruments)) &
            (self.summary_df['Interval'] == interval) &
            (self.summary_df['Risk Manager'] == risk_manager)
            ]

        if filtered_summary.empty:
            return pd.DataFrame(), go.Figure().update_layout(
                title_text="Не найдено бэктестов для анализа по заданным фильтрам")

        # 2. Агрегируем сделки со всех инструментов
        all_trades_list = []
        for _, row in filtered_summary.iterrows():
            try:
                trades_df = load_trades_from_file(os.path.join(self.logs_dir, row['File']))
                if not trades_df.empty:
                    all_trades_list.append(trades_df)
            except Exception as e:
                print(f"Ошибка при загрузке файла {row['File']}: {e}")

        if not all_trades_list:
            return pd.DataFrame(), go.Figure().update_layout(
                title_text="Не удалось загрузить сделки ни для одного инструмента")

        # Объединяем все сделки в один DataFrame и сортируем по времени закрытия
        all_trades_df = pd.concat(all_trades_list, ignore_index=True)
        all_trades_df['exit_timestamp_utc'] = pd.to_datetime(all_trades_df['exit_timestamp_utc'])
        all_trades_df.sort_values(by='exit_timestamp_utc', inplace=True)
        all_trades_df.reset_index(drop=True, inplace=True)

        # 3. Рассчитываем портфельную кривую капитала
        # Начальный капитал портфеля равен сумме начальных капиталов на каждый инструмент
        # Это допущение, что мы выделяем на каждый инструмент отдельную порцию капитала
        portfolio_initial_capital = self.initial_capital * len(filtered_summary)
        all_trades_df['cumulative_pnl'] = all_trades_df['pnl'].cumsum()
        all_trades_df['portfolio_equity'] = portfolio_initial_capital + all_trades_df['cumulative_pnl']

        # 4. ВЫЗЫВАЕМ HELPER ДЛЯ РАСЧЕТА ПОРТФЕЛЬНЫХ МЕТРИК
        portfolio_summary_series = self._calculate_portfolio_metrics(all_trades_df, portfolio_initial_capital)
        portfolio_summary_series.name = 'ИТОГО (портфель)'

        # 5. Формируем итоговую таблицу
        individual_metrics = filtered_summary[
            ['Instrument', 'PnL (Strategy %)', 'Win Rate (%)', 'Max Drawdown (%)', 'Profit Factor',
             'Total Trades']].copy()
        individual_metrics.rename(columns={'PnL (Strategy %)': 'PnL, %', 'Win Rate (%)': 'Win Rate, %',
                                           'Max Drawdown (%)': 'Max Drawdown, %'}, inplace=True)
        individual_metrics.set_index('Instrument', inplace=True)

        final_metrics_df = pd.concat([individual_metrics, portfolio_summary_series.to_frame().T])

        # 6. Строим график
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=all_trades_df.index,
            y=all_trades_df['portfolio_equity'],
            mode='lines',
            name='Портфельная кривая капитала'
        ))
        fig.update_layout(
            title_text=f"Анализ робастности '{strategy_name}' на {len(filtered_summary)} инструментах",
            xaxis_title="Количество сделок (во времени)",
            yaxis_title="Портфельный капитал"
        )

        return final_metrics_df, fig

    def compare_aggregated_strategies(
            self,
            strategy_names: List[str],
            instruments: List[str],
            interval: str,
            risk_manager: str
    ) -> Tuple[pd.DataFrame, go.Figure]:
        """
        Сравнивает агрегированные (портфельные) результаты нескольких стратегий,
        запущенных на одном и том же наборе инструментов.

        :param strategy_names: Список названий стратегий.
        :param instruments: Список тикеров инструментов, на которых строится портфель.
        :param interval: Таймфрейм.
        :param risk_manager: Тип риск-менеджера.
        :return: Кортеж (DataFrame с итоговыми портфельными метриками, Figure с портфельными графиками).
        """

        aggregated_metrics_list = []
        fig = go.Figure()

        # 1. В цикле проходим по каждой стратегии
        for strategy_name in strategy_names:
            try:
                # 2. Вызываем наш готовый метод для анализа робастности
                # Он делает всю сложную работу по агрегации и расчету для одной стратегии
                metrics_df, strategy_fig = self.analyze_instrument_robustness(
                    strategy_name=strategy_name,
                    instruments=instruments,
                    interval=interval,
                    risk_manager=risk_manager
                )

                if metrics_df.empty:
                    print(f"Нет данных для агрегации по стратегии '{strategy_name}'. Пропускаем.")
                    continue

                # 3. Извлекаем итоговую строку "ИТОГО (портфель)"
                portfolio_summary = metrics_df.loc['ИТОГО (портфель)'].copy()
                portfolio_summary.name = strategy_name  # Меняем индекс на имя стратегии
                aggregated_metrics_list.append(portfolio_summary)

                # 4. Извлекаем кривую капитала и добавляем на общий график
                # strategy_fig.data[0] - это и есть наша линия go.Scatter
                trace = strategy_fig.data[0]
                trace.name = strategy_name  # Переименовываем для легенды
                fig.add_trace(trace)

            except Exception as e:
                print(f"Ошибка при агрегации результатов для стратегии '{strategy_name}': {e}")

        if not aggregated_metrics_list:
            return pd.DataFrame(), go.Figure().update_layout(
                title_text="Не удалось собрать данные ни для одной стратегии")

        # 5. Собираем итоговую таблицу и настраиваем график
        final_metrics_df = pd.DataFrame(aggregated_metrics_list)
        # Устанавливаем имя стратегии как индекс
        final_metrics_df.index.name = "Стратегия (портфель)"

        fig.update_layout(
            title_text=f"Сравнение портфельных результатов на {len(instruments)} инструментах",
            xaxis_title="Количество сделок (во времени)",
            yaxis_title="Портфельный капитал",
            legend_title_text="Стратегии"
        )

        return final_metrics_df, fig

    def compare_two_portfolios(
            self,
            portfolio_a_params: Dict[str, Any],
            portfolio_b_params: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, go.Figure]:
        """
        Сравнивает два произвольно собранных портфеля.
        Каждый портфель определяется набором стратегий, инструментов, интервалом и РМ.
        """
        all_metrics = []
        fig = go.Figure()

        # --- Обработка Портфеля А ---
        try:
            metrics_a_df, fig_a = self.analyze_instrument_robustness(
                strategy_name=portfolio_a_params['strategy'],
                instruments=portfolio_a_params['instruments'],
                interval=portfolio_a_params['interval'],
                risk_manager=portfolio_a_params['rm']
            )
            if not metrics_a_df.empty:
                summary_a = metrics_a_df.loc['ИТОГО (портфель)'].copy()
                summary_a.name = "Портфель A"
                all_metrics.append(summary_a)

                trace_a = fig_a.data[0]
                trace_a.name = "Портфель A"
                fig.add_trace(trace_a)
        except Exception as e:
            print(f"Ошибка при расчете Портфеля A: {e}")

        # --- Обработка Портфеля B ---
        try:
            metrics_b_df, fig_b = self.analyze_instrument_robustness(
                strategy_name=portfolio_b_params['strategy'],
                instruments=portfolio_b_params['instruments'],
                interval=portfolio_b_params['interval'],
                risk_manager=portfolio_b_params['rm']
            )
            if not metrics_b_df.empty:
                summary_b = metrics_b_df.loc['ИТОГО (портфель)'].copy()
                summary_b.name = "Портфель B"
                all_metrics.append(summary_b)

                trace_b = fig_b.data[0]
                trace_b.name = "Портфель B"
                fig.add_trace(trace_b)
        except Exception as e:
            print(f"Ошибка при расчете Портфеля B: {e}")

        if not all_metrics:
            return pd.DataFrame(), go.Figure().update_layout(
                title_text="Не удалось собрать данные ни для одного портфеля")

        final_metrics_df = pd.DataFrame(all_metrics)
        fig.update_layout(
            title_text="Сравнение двух портфелей",
            xaxis_title="Количество сделок (во времени)",
            yaxis_title="Портфельный капитал",
            legend_title_text="Портфели"
        )

        return final_metrics_df, fig