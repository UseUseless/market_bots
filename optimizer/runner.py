import argparse
import optuna
import os
import pandas as pd
import logging
from datetime import datetime
from copy import deepcopy

from optimizer.objective import Objective
from optimizer.splitter import split_data_by_periods, walk_forward_generator
from strategies import AVAILABLE_STRATEGIES
from core.data_handler import HistoricLocalDataHandler
from core.backtest_engine import run_backtest_session
from config import BACKTEST_CONFIG, STRATEGY_CONFIG, RISK_CONFIG
from optimizer.search_space import SEARCH_SPACE

# Настраиваем логирование, чтобы Optuna не "спамила" в консоль
optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_wfo(args):
    """Главная функция, управляющая процессом Walk-Forward Optimization."""

    logger.info("--- Загрузка и подготовка полного набора исторических данных ---")
    data_handler = HistoricLocalDataHandler(
        events_queue=None, exchange=args.exchange, instrument_id=args.instrument,
        interval_str=args.interval, data_path="data"
    )
    full_dataset = data_handler.load_raw_data()
    if full_dataset.empty:
        logger.error(f"Не удалось загрузить данные для {args.instrument}. Оптимизация прервана.")
        return

    logger.info(f"Данные загружены. Всего {len(full_dataset)} свечей.")

    data_periods = split_data_by_periods(full_dataset, args.total_periods)

    try:
        wfo_gen = walk_forward_generator(data_periods, args.train_periods, args.test_periods)
        num_steps = args.total_periods - args.train_periods - args.test_periods + 1
    except ValueError as e:
        logger.error(f"Ошибка конфигурации WFO: {e}")
        return

    all_oos_trades = []
    step_results = []
    last_study = None  # Сохраняем study последнего шага

    param_map = _build_reverse_param_map(args.strategy, args.rm)

    logger.info(f"--- Начало Walk-Forward Optimization ({num_steps} шагов) ---")

    for train_df, test_df, step_num in wfo_gen:
        logger.info(
            f"\n--- Шаг {step_num}/{num_steps}: Обучение на {len(train_df)} свечах, тест на {len(test_df)} свечах ---")

        study = optuna.create_study(direction="maximize")
        objective = Objective(
            strategy_name=args.strategy, exchange=args.exchange, instrument=args.instrument,
            interval=args.interval, risk_manager_type=args.rm, data_slice=train_df
        )
        study.optimize(objective, n_trials=args.n_trials, show_progress_bar=True)
        last_study = study

        if not study.best_trial or study.best_value < 0:  # Добавлена проверка на отрицательный результат
            logger.warning(f"Шаг {step_num}: Optuna не нашла ни одного прибыльного решения. Пропускаем шаг.")
            continue

        best_params = study.best_params
        logger.info(f"Шаг {step_num}: Лучшие параметры найдены. Calmar (In-Sample): {study.best_value:.4f}")
        for key, value in best_params.items():
            logger.info(f"  - {key}: {value}")

        # --- УЛУЧШЕНИЕ 2 (продолжение): Применяем параметры через маппинг ---
        strategy_config_best = deepcopy(STRATEGY_CONFIG)
        risk_config_best = deepcopy(RISK_CONFIG)

        for optuna_name, value in best_params.items():
            category, config_name = param_map[optuna_name]
            if category == "strategy":
                strategy_config_best[args.strategy][config_name] = value
            elif category == "risk":
                risk_config_best[config_name] = value

        backtest_settings = {
            "strategy_class": AVAILABLE_STRATEGIES[args.strategy],
            "exchange": args.exchange, "instrument": args.instrument, "interval": args.interval,
            "risk_manager_type": args.rm,
            "initial_capital": BACKTEST_CONFIG["INITIAL_CAPITAL"],
            "commission_rate": BACKTEST_CONFIG["COMMISSION_RATE"],
            "data_dir": "data", "trade_log_path": None,
            "strategy_config": strategy_config_best, "risk_config": risk_config_best,
            "data_slice": test_df
        }

        oos_results = run_backtest_session(backtest_settings)

        if oos_results["status"] == "success" and not oos_results["trades_df"].empty:
            logger.info(f"Шаг {step_num}: Тест на OOS-данных дал {len(oos_results['trades_df'])} сделок.")
            all_oos_trades.append(oos_results["trades_df"])
        else:
            logger.warning(f"Шаг {step_num}: Тест на OOS-данных не дал сделок.")

        step_results.append({"step": step_num, "best_value_in_sample": study.best_value, **best_params})

    if not all_oos_trades:
        logger.error("Оптимизация завершена, но ни на одном из шагов не было получено сделок на тестовых данных.")
        return

    # --- ИЗМЕНЕНИЕ: Полностью переработанный блок отчетности ---

    logger.info("\n--- WFO Завершена. Генерация итоговых отчетов ---")

    # 1. Настройка путей и имен файлов
    report_dir = "optimizer/reports"
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{report_dir}/{timestamp}_WFO_{args.strategy}_{args.instrument}"

    # 2. Главный отчет по "склеенным" OOS-результатам
    final_trades_df = pd.concat(all_oos_trades, ignore_index=True)
    from analyzer import BacktestAnalyzer
    analyzer = BacktestAnalyzer(
        trades_df=final_trades_df, historical_data=full_dataset,
        initial_capital=BACKTEST_CONFIG["INITIAL_CAPITAL"], interval=args.interval,
        risk_manager_type=args.rm, report_dir=report_dir, exchange=args.exchange
    )
    analyzer.generate_report(base_filename.split('/')[-1])  # Передаем только имя файла

    # 3. Отчет по стабильности параметров
    pd.DataFrame(step_results).to_csv(f"{base_filename}_steps_summary.csv", index=False)
    logger.info(f"Сводка по шагам WFO сохранена в: {base_filename}_steps_summary.csv")

    # 4. Визуальные отчеты Optuna по ПОСЛЕДНЕМУ шагу
    if last_study:
        logger.info("Сохранение визуальных отчетов Optuna для последнего шага WFO...")
        try:
            fig_history = optuna.visualization.plot_optimization_history(last_study)
            fig_history.write_html(f"{base_filename}_last_step_history.html")

            fig_importance = optuna.visualization.plot_param_importances(last_study)
            fig_importance.write_html(f"{base_filename}_last_step_importance.html")

            logger.info("HTML-отчеты Optuna успешно сохранены.")
        except (ImportError, ModuleNotFoundError):
            logger.warning("Для сохранения HTML-отчетов установите plotly: pip install plotly")
        except Exception as e:
            logger.error(f"Не удалось сохранить HTML-отчеты Optuna: {e}")

    logger.info(f"Все отчеты сохранены в папку: {report_dir}")


def main():
    parser = argparse.ArgumentParser(description="Менеджер оптимизации параметров стратегий.")
    parser.add_argument("--strategy", type=str, required=True,
                        help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True, help="Тикер/символ инструмента.")
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм.")
    parser.add_argument("--rm", dest="risk_manager_type", type=str, default="FIXED", choices=["FIXED", "ATR"])
    parser.add_argument("--n_trials", type=int, default=100, help="Количество итераций оптимизации.")
    parser.add_argument("--total_periods", type=int, required=True, help="На сколько частей делить весь датасет.")
    parser.add_argument("--train_periods", type=int, required=True, help="Сколько частей использовать для обучения.")
    parser.add_argument("--test_periods", type=int, default=1,
                        help="Сколько частей использовать для теста (по умолчанию: 1).")
    args = parser.parse_args()

    run_wfo(args)

if __name__ == "__main__":
    main()