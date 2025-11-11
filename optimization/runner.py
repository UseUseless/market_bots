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

from optimization.objective import Objective
from optimization.splitter import split_data_by_periods, walk_forward_generator
from strategies import AVAILABLE_STRATEGIES
from core.risk_manager import AVAILABLE_RISK_MANAGERS
from core.data_handler import HistoricLocalDataHandler
from core.backtest_engine import run_backtest_session
from optimization.metrics import METRIC_CONFIG
from config import BACKTEST_CONFIG

# Настраиваем логирование, чтобы Optuna не "спамила" в консоль
optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TqdmLoggingHandler(logging.Handler):
    """
    Перенаправляет вывод логов через tqdm.write(), чтобы не ломать
    отображение прогресс-баров.
    """
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
    """
    Создает список HTML-форматированных строк с параметрами для каждой итерации.
    Это будет использоваться для кастомных всплывающих подсказок на графике.
    """
    hover_texts = []
    for trial in trials:
        params_str = "<br>".join([f"&nbsp;&nbsp;{key}: {value}" for key, value in trial.params.items()])
        hover_texts.append(params_str)
    return hover_texts


def _setup_and_prepare_data(args: argparse.Namespace) -> Tuple[pd.DataFrame, List[pd.DataFrame], int]:
    """
    Шаг 1: Загружает и подготавливает все необходимые данные для WFO.
    Возвращает полный датасет, список срезов данных и количество шагов WFO.
    """
    logger.info("--- Загрузка и подготовка полного набора исторических данных ---")
    data_handler = HistoricLocalDataHandler(
        events_queue=None, exchange=args.exchange, instrument_id=args.instrument,
        interval_str=args.interval, data_path="data"
    )
    full_dataset = data_handler.load_raw_data()
    if full_dataset.empty:
        raise FileNotFoundError(f"Не удалось загрузить данные для {args.instrument}. Оптимизация прервана.")

    logger.info(f"Данные загружены. Всего {len(full_dataset)} свечей.")

    data_periods = split_data_by_periods(full_dataset, args.total_periods)

    num_steps = args.total_periods - args.train_periods - args.test_periods + 1
    if num_steps <= 0:
        raise ValueError("Недостаточно данных для WFO с заданными параметрами (total_periods слишком мал).")

    return full_dataset, data_periods, num_steps


def _estimate_execution_time(args: argparse.Namespace, data_periods: List[pd.DataFrame], num_steps: int):
    """
    Шаг 2: Проводит пробный запуск, рассчитывает и выводит примерное время выполнения.
    """
    logger.info("--- Расчет примерного времени выполнения ---")
    strategy_class = AVAILABLE_STRATEGIES[args.strategy]
    rm_class = AVAILABLE_RISK_MANAGERS[args.rm]

    dummy_settings = {
        "strategy_class": strategy_class, "exchange": args.exchange, "instrument": args.instrument,
        "interval": args.interval, "risk_manager_type": args.rm,
        "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"], "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
        "data_dir": "data", "trade_log_path": None,
        "strategy_params": strategy_class.get_default_params(),
        "risk_manager_params": rm_class.get_default_params(),
        "data_slice": data_periods[0]
    }

    start_time = time.time()
    run_backtest_session(dummy_settings)
    time_per_trial = time.time() - start_time
    logger.info(f"Время выполнения одного бэктеста: {time_per_trial:.4f} сек.")

    total_trials = args.n_trials * num_steps
    estimated_seconds = total_trials * time_per_trial
    estimated_time_str = str(timedelta(seconds=int(estimated_seconds)))

    console = Console()
    console.print("\n[bold cyan]--- План Оптимизации ---[/bold cyan]")
    console.print(f"Время начала: [bold]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/bold]")
    console.print(f"Количество шагов WFO: [bold]{num_steps}[/bold]")
    console.print(f"Итераций на каждом шаге: [bold]{args.n_trials}[/bold]")
    console.print(f"Общее количество бэктестов: [bold yellow]{total_trials}[/bold yellow]")
    console.print(f"Примерное время выполнения: [bold magenta]~ {estimated_time_str}[/bold magenta] (Ч:М:С)")


def _run_wfo_loop(args: argparse.Namespace, data_periods: List[pd.DataFrame], num_steps: int) -> Tuple[List[pd.DataFrame], List[Dict], Any]:
    """
    Шаг 3: Запускает основной цикл Walk-Forward Optimization.
    Возвращает результаты: список OOS сделок, сводку по шагам и последний объект study.
    """
    wfo_gen = walk_forward_generator(data_periods, args.train_periods, args.test_periods)
    all_oos_trades = []
    step_results = []
    last_study = None

    console = Console()
    console.print(f"\n[bold]--- Начало Walk-Forward Optimization ({num_steps} шагов) ---[/bold]")

    for train_df, test_df, step_num in tqdm(wfo_gen, total=num_steps, desc="Общий прогресс WFO"):
        logger.info(f"\n--- Шаг {step_num}/{num_steps}: Обучение на {len(train_df)} свечах, тест на {len(test_df)} свечах ---")

        metric_info = METRIC_CONFIG[args.metric]
        study = optuna.create_study(direction=metric_info["direction"])

        strategy_class = AVAILABLE_STRATEGIES[args.strategy]
        objective = Objective(
            strategy_class=strategy_class, exchange=args.exchange, instrument=args.instrument,
            interval=args.interval, risk_manager_type=args.rm, data_slice=train_df, metric=args.metric
        )

        study.optimize(objective, n_trials=args.n_trials, n_jobs=-1, show_progress_bar=True)
        last_study = study

        if not study.best_trial or study.best_value < 0:
            logger.warning(f"Шаг {step_num}: Optuna не нашла прибыльного решения. Пропускаем OOS-тест.")
            continue

        metric_name = metric_info.get("name", args.metric)

        best_params = study.best_params
        # Используем динамическое имя в логе
        logger.info(f"Шаг {step_num}: Лучшие параметры найдены. {metric_name} (In-Sample): {study.best_value:.4f}")
        for key, value in best_params.items():
            logger.info(f"  - {key}: {value}")

        rm_class = AVAILABLE_RISK_MANAGERS[args.rm]
        strategy_default_params = strategy_class.get_default_params()
        rm_default_params = rm_class.get_default_params()

        best_strategy_params = {k: v for k, v in best_params.items() if not k.startswith("rm_")}
        best_rm_params = {k[3:]: v for k, v in best_params.items() if k.startswith("rm_")}

        final_strategy_params = {**strategy_default_params, **best_strategy_params}
        final_rm_params = {**rm_default_params, **best_rm_params}

        backtest_settings = {
            "strategy_class": strategy_class, "exchange": args.exchange, "instrument": args.instrument,
            "interval": args.interval, "risk_manager_type": args.rm,
            "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"], "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
            "data_dir": "data", "trade_log_path": None,
            "strategy_params": final_strategy_params, "risk_manager_params": final_rm_params,
            "data_slice": test_df
        }

        oos_results = run_backtest_session(backtest_settings)
        if oos_results["status"] == "success" and not oos_results["trades_df"].empty:
            logger.info(f"Шаг {step_num}: Тест на OOS-данных дал {len(oos_results['trades_df'])} сделок.")
            all_oos_trades.append(oos_results["trades_df"])
        else:
            logger.warning(f"Шаг {step_num}: Тест на OOS-данных не дал сделок.")

        step_results.append({"step": step_num, "best_value_in_sample": study.best_value, **best_params})

    return all_oos_trades, step_results, last_study


def _generate_final_reports(args: argparse.Namespace, all_oos_trades: List[pd.DataFrame], full_dataset: pd.DataFrame, step_results: List[Dict], last_study: Any):
    """
    Шаг 4: Генерирует и сохраняет все итоговые отчеты по результатам WFO.
    """
    if not all_oos_trades:
        logger.error("Оптимизация завершена без сделок на тестовых данных. Отчеты не будут сгенерированы.")
        return

    logger.info("\n--- WFO Завершена. Генерация итоговых отчетов ---")
    from analyzer import BacktestAnalyzer # Локальный импорт для избежания циклических зависимостей

    report_dir = "optimization/reports"
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{report_dir}/{timestamp}_WFO_{args.strategy}_{args.instrument}"

    final_trades_df = pd.concat(all_oos_trades, ignore_index=True)
    analyzer = BacktestAnalyzer(
        trades_df=final_trades_df, historical_data=full_dataset,
        initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"], interval=args.interval,
        risk_manager_type=args.rm, report_dir=report_dir, exchange=args.exchange
    )
    analyzer.generate_report(os.path.basename(base_filename))

    pd.DataFrame(step_results).to_csv(f"{base_filename}_steps_summary.csv", index=False)
    logger.info(f"Сводка по шагам WFO сохранена в: {base_filename}_steps_summary.csv")

    if last_study:
        logger.info("Сохранение визуальных отчетов Optuna для последнего шага WFO...")
        try:
            fig_history = optuna.visualization.plot_optimization_history(last_study)
            hover_texts = _create_hover_text_for_trials(last_study.trials)
            if fig_history.data:
                fig_history.data[0].customdata = hover_texts
                fig_history.data[0].hovertemplate = (
                    "<b>Trial: %{x}</b><br>Value: %{y}<br><br><b>Parameters:</b><br>%{customdata}<extra></extra>"
                )
            fig_history.write_html(f"{base_filename}_last_step_history.html")

            completed_trials = [t for t in last_study.trials if t.state == optuna.trial.TrialState.COMPLETE]
            if len(completed_trials) >= 2:
                fig_importance = optuna.visualization.plot_param_importances(last_study)
                fig_importance.write_html(f"{base_filename}_last_step_importance.html")
                logger.info("HTML-отчеты (History, Importance) успешно сохранены.")
            else:
                logger.warning("Недостаточно успешных итераций для расчета важности параметров. Отчет 'Importance' пропущен.")
        except Exception as e:
            logger.error(f"Не удалось сохранить HTML-отчеты Optuna: {e}", exc_info=True)


def run_wfo(args):
    """
    Главная функция-оркестратор, управляющая процессом Walk-Forward Optimization.
    """

    root_logger = logging.getLogger()
    original_level = root_logger.level

    default_handler = None
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, TqdmLoggingHandler):
            default_handler = handler
            break

    if default_handler:
        root_logger.removeHandler(default_handler)

    root_logger.setLevel(logging.WARNING)
    tqdm_handler = TqdmLoggingHandler()
    root_logger.addHandler(tqdm_handler)

    logger.info("Уровень логирования временно установлен на WARNING и перенаправлен через tqdm.")

    try:
        full_dataset, data_periods, num_steps = _setup_and_prepare_data(args)
        _estimate_execution_time(args, data_periods, num_steps)
        all_oos_trades, step_results, last_study = _run_wfo_loop(args, data_periods, num_steps)
        _generate_final_reports(args, all_oos_trades, full_dataset, step_results, last_study)

    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Ошибка подготовки WFO: {e}")

    except KeyboardInterrupt:
        logger.warning("\nОптимизация прервана пользователем. Завершение работы...")

    except Exception:
        logger.critical("Произошла непредвиденная ошибка в процессе WFO!", exc_info=True)
    finally:
        root_logger.removeHandler(tqdm_handler)
        if default_handler:
            root_logger.addHandler(default_handler)
        root_logger.setLevel(original_level)
        logger.info("Настройки логирования восстановлены.")

def main():
    """
    Парсит аргументы командной строки и запускает процесс WFO.
    """
    parser = argparse.ArgumentParser(description="Менеджер оптимизации параметров стратегий (WFO).")
    parser.add_argument("--strategy", type=str, required=True, help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True, help="Тикер/символ инструмента.")
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм.")
    parser.add_argument("--rm", type=str, default="FIXED", choices=list(AVAILABLE_RISK_MANAGERS.keys()))
    parser.add_argument("--n_trials", type=int, default=100, help="Количество итераций оптимизации на каждом шаге.")
    parser.add_argument("--total_periods", type=int, required=True, help="На сколько частей делить весь датасет.")
    parser.add_argument("--train_periods", type=int, required=True, help="Сколько частей использовать для обучения.")
    parser.add_argument("--test_periods", type=int, default=1, help="Сколько частей использовать для теста (по умолчанию: 1).")
    parser.add_argument("--metric", type=str, default="calmar_ratio", choices=list(METRIC_CONFIG.keys()), help="Целевая метрика для оптимизации.")
    args = parser.parse_args()

    run_wfo(args)

if __name__ == "__main__":
    main()