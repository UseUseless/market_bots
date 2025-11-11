import argparse
import optuna
import os
import pandas as pd
import logging
import time
from datetime import datetime, timedelta
from tqdm import tqdm
from typing import List, Tuple, Dict, Any

from rich.console import Console
from rich.table import Table

from optimization.objective import Objective
from optimization.splitter import split_data_by_periods
from strategies import AVAILABLE_STRATEGIES
from core.risk_manager import AVAILABLE_RISK_MANAGERS
from core.data_handler import HistoricLocalDataHandler
from core.backtest_engine import run_backtest_session
from optimization.metrics import MetricsCalculator, METRIC_CONFIG
from config import BACKTEST_CONFIG, EXCHANGE_SPECIFIC_CONFIG, PATH_CONFIG

logger = logging.getLogger(__name__)

class TqdmLoggingHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            self.handleError(record)

def _create_hover_text_for_trials(trials: List[optuna.trial.FrozenTrial]) -> List[str]:
    hover_texts = []
    for trial in trials:
        params_str = "<br>".join([f"&nbsp;&nbsp;{key}: {value}" for key, value in trial.params.items()])
        hover_texts.append(params_str)
    return hover_texts

def _prepare_all_wfo_data(args: argparse.Namespace, instrument_list: List[str]) -> Tuple[
    Dict[str, List[pd.DataFrame]], int]:
    logger.info(f"--- Предварительная загрузка и нарезка данных для {len(instrument_list)} инструментов ---")
    all_instrument_periods = {}
    num_steps = 0

    for instrument in tqdm(instrument_list, desc="Подготовка данных"):
        data_handler = HistoricLocalDataHandler(
            events_queue=None, exchange=args.exchange, instrument_id=instrument,
            interval_str=args.interval, data_path=PATH_CONFIG["DATA_DIR"]
        )
        full_dataset = data_handler.load_raw_data()
        if full_dataset.empty:
            logger.warning(f"Не удалось загрузить данные для {instrument}. Инструмент будет пропущен.")
            continue

        data_periods = split_data_by_periods(full_dataset, args.total_periods)
        all_instrument_periods[instrument] = data_periods

    if not all_instrument_periods:
        raise FileNotFoundError("Не удалось загрузить данные ни для одного инструмента.")

    first_instrument_periods = next(iter(all_instrument_periods.values()))
    num_steps = len(first_instrument_periods) - args.train_periods - args.test_periods + 1
    if num_steps <= 0:
        raise ValueError("Недостаточно данных для WFO с заданными параметрами.")

    return all_instrument_periods, num_steps


def _estimate_execution_time(args: argparse.Namespace, num_steps: int, num_instruments: int = 1):
    """
    Проводит пробный запуск и выводит примерное время выполнения.
    """
    logger.info("--- Расчет примерного времени выполнения ---")
    strategy_class = AVAILABLE_STRATEGIES[args.strategy]
    rm_class = AVAILABLE_RISK_MANAGERS[args.rm]

    # Надежно получаем имя первого .parquet файла, а не просто первого файла в папке
    if args.portfolio_path:
        try:
            # Ищем первый файл, который заканчивается на .parquet
            first_parquet_file = next(f for f in os.listdir(args.portfolio_path) if f.lower().endswith('.parquet'))
            instrument_to_test = os.path.splitext(first_parquet_file)[0] # Убираем расширение
        except (StopIteration, IndexError):
            logger.warning("Не удалось найти .parquet файл в папке портфеля для оценки времени.")
            return
    else:
        instrument_to_test = args.instrument

    # Загружаем данные для одного инструмента, чтобы получить срез для теста
    data_handler = HistoricLocalDataHandler(None, args.exchange, instrument_to_test, args.interval,
                                            PATH_CONFIG["DATA_DIR"])
    test_data = data_handler.load_raw_data()
    if test_data.empty:
        logger.warning("Не удалось загрузить данные для оценки времени, расчет пропущен.")
        return

    test_data_slice = split_data_by_periods(test_data, args.total_periods)[0]

    dummy_settings = {
        "strategy_class": strategy_class, "exchange": args.exchange, "instrument": instrument_to_test,
        "interval": args.interval, "risk_manager_type": args.rm,
        "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"] / num_instruments,
        "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
        "data_dir": PATH_CONFIG["DATA_DIR"], "trade_log_path": None,
        "strategy_params": strategy_class.get_default_params(),
        "risk_manager_params": rm_class.get_default_params(),
        "data_slice": test_data_slice
    }

    start_time = time.time()
    run_backtest_session(dummy_settings)
    time_per_backtest = time.time() - start_time
    logger.info(f"Время выполнения одного бэктеста: {time_per_backtest:.4f} сек.")

    total_backtests = args.n_trials * num_steps * num_instruments
    estimated_seconds = total_backtests * time_per_backtest
    estimated_time_str = str(timedelta(seconds=int(estimated_seconds)))

    console = Console()
    console.print("\n[bold cyan]--- План Оптимизации ---[/bold cyan]")
    console.print(f"Время начала: [bold]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]")
    console.print(f"Инструментов в портфеле: [bold]{num_instruments}[/bold]")
    console.print(f"Количество шагов WFO: [bold]{num_steps}[/bold]")
    console.print(f"Итераций на каждом шаге: [bold]{args.n_trials}[/bold]")
    console.print(f"Общее количество бэктестов: [bold yellow]{total_backtests}[/bold yellow]")
    console.print(f"Примерное время выполнения: [bold magenta]~ {estimated_time_str}[/bold magenta] (Ч:М:С)")


def _select_best_from_pareto(trials: List[optuna.trial.FrozenTrial],
                             tie_breaker_metric: str = "calmar_ratio") -> optuna.trial.FrozenTrial:
    if not trials:
        return None
    direction = METRIC_CONFIG[tie_breaker_metric]['direction']
    best_trial = max(
        trials,
        key=lambda t: t.user_attrs.get(tie_breaker_metric, -1e9)
    ) if direction == 'maximize' else min(
        trials,
        key=lambda t: t.user_attrs.get(tie_breaker_metric, 1e9)
    )
    return best_trial


def _run_wfo_loop(args: argparse.Namespace, all_instrument_periods: Dict[str, List[pd.DataFrame]], num_steps: int,
                  base_filename: str) -> Tuple[List[pd.DataFrame], List[Dict], Any]:
    all_oos_trades = []
    step_results = []
    last_study = None
    console = Console()
    console.print(f"\n[bold]--- Начало Walk-Forward Optimization ({num_steps} шагов) ---[/bold]")

    instrument_list = list(all_instrument_periods.keys())

    for step_num in tqdm(range(1, num_steps + 1), desc="Общий прогресс WFO"):
        train_start_idx = step_num - 1
        train_end_idx = train_start_idx + args.train_periods
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + args.test_periods

        train_slices = {
            instrument: pd.concat(periods[train_start_idx:train_end_idx], ignore_index=True)
            for instrument, periods in all_instrument_periods.items()
        }
        test_slices = {
            instrument: pd.concat(periods[test_start_idx:test_end_idx], ignore_index=True)
            for instrument, periods in all_instrument_periods.items()
        }

        tqdm.write(f"\n--- Шаг {step_num}/{num_steps} ---")

        metrics_to_optimize = args.metrics
        if len(metrics_to_optimize) == 1:
            direction = METRIC_CONFIG[metrics_to_optimize[0]]["direction"]
            study = optuna.create_study(direction=direction)
        else:
            directions = [METRIC_CONFIG[m]["direction"] for m in metrics_to_optimize]
            study = optuna.create_study(directions=directions)

        strategy_class = AVAILABLE_STRATEGIES[args.strategy]
        objective = Objective(
            strategy_class=strategy_class, exchange=args.exchange, interval=args.interval,
            risk_manager_type=args.rm, train_data_slices=train_slices, metrics=metrics_to_optimize
        )
        study.optimize(objective, n_trials=args.n_trials, n_jobs=-1, show_progress_bar=True)
        last_study = study

        if not study.best_trials:
            tqdm.write(f"Шаг {step_num}: Optuna не нашла ни одного решения. Пропускаем OOS-тест.")
            continue

        best_trial: optuna.trial.FrozenTrial
        if len(study.directions) > 1:
            pareto_front = study.best_trials
            tqdm.write(f"Шаг {step_num}: Найдено {len(pareto_front)} недоминируемых решений (фронт Парето).")

            pareto_table = Table(title=f"Шаг {step_num}: Фронт Парето")
            pareto_table.add_column("Trial #", style="green")
            for i, metric_key in enumerate(metrics_to_optimize):
                pareto_table.add_column(METRIC_CONFIG[metric_key]['name'], style="cyan", justify="right")
            for trial in pareto_front:
                row = [str(trial.number)] + [f"{trial.values[i]:.4f}" for i in range(len(metrics_to_optimize))]
                pareto_table.add_row(*row)
            console.print(pareto_table)

            pareto_data = []
            for trial in pareto_front:
                row = {"trial_number": trial.number}
                for i, metric_key in enumerate(metrics_to_optimize):
                    row[metric_key] = trial.values[i]
                row.update(trial.params)
                pareto_data.append(row)

            pareto_filename = f"{base_filename}_step_{step_num}_pareto_front.csv"
            pd.DataFrame(pareto_data).to_csv(pareto_filename, index=False)
            tqdm.write(f"Полный фронт Парето для шага {step_num} сохранен в: {os.path.basename(pareto_filename)}")

            best_trial = _select_best_from_pareto(pareto_front)
            tie_breaker_value = best_trial.user_attrs.get('calmar_ratio', float('nan'))
            tqdm.write(
                f"Выбрано решение #{best_trial.number} по решающей метрике 'Calmar Ratio' ({tie_breaker_value:.4f}) для OOS-теста.")
        else:
            best_trial = study.best_trial

        table = Table(title=f"Шаг {step_num}: Лучшие In-Sample результаты (Trial #{best_trial.number})")
        table.add_column("Метрика", style="cyan")
        table.add_column("Значение", style="magenta", justify="right")
        for key, value in sorted(best_trial.user_attrs.items()):
            metric_display_name = METRIC_CONFIG[key]['name']
            is_target = key in metrics_to_optimize
            display_name = f"[bold]{metric_display_name}[/bold]" if is_target else metric_display_name
            table.add_row(display_name, f"{value:.4f}")
        console.print(table)

        step_summary = {"step": step_num, "best_trial_number": best_trial.number, **best_trial.user_attrs,
                        **best_trial.params}
        step_results.append(step_summary)

        all_oos_instrument_trades = []
        capital_per_instrument = BACKTEST_CONFIG["INITIAL_CAPITAL"] / len(instrument_list)

        for instrument, instrument_data_slice_oos in test_slices.items():
            if instrument_data_slice_oos.empty:
                continue

            rm_class = AVAILABLE_RISK_MANAGERS[args.rm]
            strategy_default_params = strategy_class.get_default_params()
            rm_default_params = rm_class.get_default_params()
            best_strategy_params = {k: v for k, v in best_trial.params.items() if not k.startswith("rm_")}
            best_rm_params = {k[3:]: v for k, v in best_trial.params.items() if k.startswith("rm_")}
            final_strategy_params = {**strategy_default_params, **best_strategy_params}
            final_rm_params = {**rm_default_params, **best_rm_params}

            backtest_settings = {
                "strategy_class": strategy_class, "exchange": args.exchange, "instrument": instrument,
                "interval": args.interval, "risk_manager_type": args.rm,
                "initial_capital": capital_per_instrument,
                "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
                "strategy_params": final_strategy_params, "risk_manager_params": final_rm_params,
                "data_slice": instrument_data_slice_oos,
                "data_dir": PATH_CONFIG["DATA_DIR"]
            }

            oos_results = run_backtest_session(backtest_settings)
            if oos_results["status"] == "success" and not oos_results["trades_df"].empty:
                all_oos_instrument_trades.append(oos_results["trades_df"])

        if all_oos_instrument_trades:
            step_oos_trades_df = pd.concat(all_oos_instrument_trades, ignore_index=True)
            tqdm.write(
                f"Шаг {step_num}: Тест на OOS-данных дал {len(step_oos_trades_df)} сделок по {len(instrument_list)} инструментам.")
            all_oos_trades.append(step_oos_trades_df)
        else:
            tqdm.write(f"Шаг {step_num}: Тест на OOS-данных не дал сделок.")

    return all_oos_trades, step_results, last_study

def _save_optuna_visualizations(study: optuna.Study, base_filename: str, args: argparse.Namespace):
    if not study:
        return
    logger.info("Сохранение визуальных отчетов Optuna для последнего шага WFO...")
    is_multi_objective = len(study.directions) > 1
    if is_multi_objective:
        try:
            fig_pareto = optuna.visualization.plot_pareto_front(study, target_names=[METRIC_CONFIG[m]['name'] for m in
                                                                                     args.metrics])
            fig_pareto.write_html(f"{base_filename}_last_step_pareto_front.html")
            logger.info("HTML-отчет (Pareto Front) успешно сохранен.")
        except (ValueError, ImportError) as e:
            logger.error(f"Не удалось сохранить HTML-отчет фронта Парето: {e}", exc_info=True)
    try:
        target_for_plot = None
        if is_multi_objective:
            target_for_plot = lambda t: t.values[0]
            target_name_for_plot = METRIC_CONFIG[args.metrics[0]]['name']
        else:
            target_name_for_plot = "Objective Value"
        fig_history = optuna.visualization.plot_optimization_history(study, target=target_for_plot,
                                                                     target_name=target_name_for_plot)
        if fig_history.data and not is_multi_objective:
            trials = study.trials
            hover_texts_trials = _create_hover_text_for_trials(trials)
            fig_history.data[0].customdata = hover_texts_trials
            fig_history.data[0].hovertemplate = (
                "<b>Trial: %{x}</b><br>Value: %{y}<br><br><b>Parameters:</b><br>%{customdata}<extra></extra>")
            if len(fig_history.data) > 1:
                best_trials_so_far = []
                current_best_value = None
                current_best_trial = None
                is_maximize = study.direction == optuna.study.StudyDirection.MAXIMIZE
                for trial in trials:
                    if trial.state != optuna.trial.TrialState.COMPLETE:
                        best_trials_so_far.append(current_best_trial if current_best_trial else trial)
                        continue
                    if current_best_trial is None or (is_maximize and trial.value > current_best_value) or (
                            not is_maximize and trial.value < current_best_value):
                        current_best_trial = trial
                        current_best_value = trial.value
                    best_trials_so_far.append(current_best_trial)
                valid_best_trials = [t for t in best_trials_so_far if t is not None]
                if valid_best_trials:
                    hover_texts_best = _create_hover_text_for_trials(valid_best_trials)
                    fig_history.data[1].customdata = hover_texts_best
                    fig_history.data[1].hovertemplate = (
                        "<b>Trial: %{x}</b><br>Best Value: %{y}<br><br><b>Best Parameters Found So Far:</b><br>%{customdata}<extra></extra>")
        fig_history.write_html(f"{base_filename}_last_step_history.html")
        if len(study.get_trials(deepcopy=False, states=[optuna.trial.TrialState.COMPLETE])) >= 2:
            fig_importance = optuna.visualization.plot_param_importances(study, target=target_for_plot,
                                                                         target_name=target_name_for_plot)
            fig_importance.write_html(f"{base_filename}_last_step_importance.html")
            logger.info("HTML-отчеты (History, Importance) успешно сохранены.")
    except Exception as e:
        logger.error(f"Не удалось сохранить HTML-отчеты History/Importance: {e}", exc_info=True)

def _generate_final_reports(args: argparse.Namespace, all_oos_trades: List[pd.DataFrame],
                            step_results: List[Dict], last_study: Any, base_filename: str):
    if not all_oos_trades:
        logger.error("Оптимизация завершена без сделок на тестовых данных. Отчеты не будут сгенерированы.")
        return

    logger.info("\n--- WFO Завершена. Генерация итоговых отчетов ---")
    from analyzer import BacktestAnalyzer

    final_trades_df = pd.concat(all_oos_trades, ignore_index=True)

    annual_factor = EXCHANGE_SPECIFIC_CONFIG[args.exchange]["SHARPE_ANNUALIZATION_FACTOR"]
    final_calculator = MetricsCalculator(final_trades_df, BACKTEST_CONFIG["INITIAL_CAPITAL"], annual_factor)

    final_oos_metrics = {}
    if final_calculator.is_valid:
        for metric_key in METRIC_CONFIG.keys():
            final_oos_metrics[metric_key] = final_calculator.calculate(metric_key)

    if args.portfolio_path:
        try:
            first_parquet_file = next(f for f in os.listdir(args.portfolio_path) if f.lower().endswith('.parquet'))
            instrument_for_bh = os.path.splitext(first_parquet_file)[0]
        except (StopIteration, IndexError):
            logger.error("Не удалось найти эталонный инструмент для Buy&Hold графика.")
            instrument_for_bh = None
    else:
        instrument_for_bh = args.instrument

    full_bh_dataset = pd.DataFrame()  # Создаем пустой DataFrame на случай ошибки
    if instrument_for_bh:
        data_handler_bh = HistoricLocalDataHandler(None, args.exchange, instrument_for_bh, args.interval,
                                                   PATH_CONFIG["DATA_DIR"])
        full_bh_dataset = data_handler_bh.load_raw_data()

    analyzer = BacktestAnalyzer(
        trades_df=final_trades_df, historical_data=full_bh_dataset,
        initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"], interval=args.interval,
        risk_manager_type=args.rm, report_dir=os.path.dirname(base_filename), exchange=args.exchange
    )

    analyzer.generate_report(
        report_filename=os.path.basename(base_filename),
        target_metric=args.metrics[0],
        wfo_results=final_oos_metrics
    )

    pd.DataFrame(step_results).to_csv(f"{base_filename}_steps_summary.csv", index=False)
    logger.info(f"Сводка по шагам WFO сохранена в: {base_filename}_steps_summary.csv")

    _save_optuna_visualizations(last_study, base_filename, args)

def run_wfo(args):
    """
    Главная функция-оркестратор, управляющая процессом Walk-Forward Optimization.
    """
    from utils.logger_config import setup_global_logging
    setup_global_logging(mode='tqdm', log_level=logging.WARNING)

    try:
        if args.portfolio_path:
            if not os.path.isdir(args.portfolio_path):
                raise FileNotFoundError(f"Указанный путь к портфелю не является папкой: {args.portfolio_path}")
            instrument_list = sorted(
                [f.replace('.parquet', '') for f in os.listdir(args.portfolio_path) if f.endswith('.parquet')])
            if not instrument_list:
                raise ValueError(f"В папке портфеля не найдено .parquet файлов.")
        else:
            instrument_list = [args.instrument]

        logger.info(f"Запуск WFO для {len(instrument_list)} инструментов.")

        all_instrument_periods, num_steps = _prepare_all_wfo_data(args, instrument_list)

        _estimate_execution_time(args, num_steps, len(instrument_list))

        report_dir = os.path.join("optimization", "reports")
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        instrument_name = args.instrument if args.instrument else f"Portfolio_{len(instrument_list)}"
        base_filename = f"{report_dir}/{timestamp}_WFO_{args.strategy}_{instrument_name}"

        all_oos_trades, step_results, last_study = _run_wfo_loop(args, all_instrument_periods, num_steps, base_filename)

        _generate_final_reports(args, all_oos_trades, step_results, last_study, base_filename)

    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Ошибка подготовки WFO: {e}")
    except KeyboardInterrupt:
        logger.warning("\nОптимизация прервана пользователем. Завершение работы...")
    except Exception:
        logger.critical("Произошла непредвиденная ошибка в процессе WFO!", exc_info=True)
    finally:
        from utils.logger_config import setup_global_logging
        setup_global_logging(mode='default', log_level=logging.INFO)
        print("\nНастройки логирования восстановлены.")

def main():
    """
    Парсит аргументы командной строки и запускает процесс WFO.
    """
    parser = argparse.ArgumentParser(description="Менеджер оптимизации параметров стратегий (WFO).")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--instrument", type=str, help="Тикер/символ ОДНОГО инструмента.")
    group.add_argument("--portfolio-path", type=str,
                       help="Путь к папке с .parquet файлами для портфельной оптимизации.")

    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм.")
    parser.add_argument("--strategy", type=str, required=True,
                        help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument("--rm", type=str, default="FIXED", choices=list(AVAILABLE_RISK_MANAGERS.keys()))

    parser.add_argument("--metrics", type=str, nargs='+', default=["calmar_ratio"], choices=list(METRIC_CONFIG.keys()),
                        help="Одна или несколько целевых метрик для оптимизации.")

    parser.add_argument("--n_trials", type=int, default=100, help="Количество итераций оптимизации на каждом шаге.")
    parser.add_argument("--total_periods", type=int, required=True, help="На сколько частей делить весь датасет.")
    parser.add_argument("--train_periods", type=int, required=True, help="Сколько частей использовать для обучения.")
    parser.add_argument("--test_periods", type=int, default=1,
                        help="Сколько частей использовать для теста (по умолчанию: 1).")

    args = parser.parse_args()
    run_wfo(args)

if __name__ == "__main__":
    main()