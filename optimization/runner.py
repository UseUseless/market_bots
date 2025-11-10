import argparse
import optuna
import os
import pandas as pd
import logging
from datetime import datetime
from copy import deepcopy
from typing import List

from optimization.objective import Objective
from optimization.splitter import split_data_by_periods, walk_forward_generator
from optimization.search_space import SEARCH_SPACE

from strategies import AVAILABLE_STRATEGIES
from core.data_handler import HistoricLocalDataHandler
from core.backtest_engine import run_backtest_session
from config import BACKTEST_CONFIG, STRATEGY_CONFIG, RISK_CONFIG

# Настраиваем логирование, чтобы Optuna не "спамила" в консоль
optuna.logging.set_verbosity(optuna.logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _create_hover_text_for_trials(trials: List[optuna.trial.FrozenTrial]) -> List[str]:
    """
    Создает список HTML-форматированных строк с параметрами для каждой итерации.
    Это будет использоваться для кастомных всплывающих подсказок на графике.
    """
    hover_texts = []
    for trial in trials:
        # Собираем параметры в одну строку, разделяя их тегом <br> (перенос строки в HTML)
        params_str = "<br>".join([f"&nbsp;&nbsp;{key}: {value}" for key, value in trial.params.items()])
        hover_texts.append(params_str)
    return hover_texts

def _build_reverse_param_map(strategy_class_name: str, rm_type: str) -> dict:
    """
    Создает обратный словарь для быстрого применения найденных параметров.
    Ключ: имя параметра в Optuna, Значение: (категория, имя в конфиге).
    Пример: {'ema_fast': ('strategy', 'ema_fast_period')}
    """
    reverse_map = {}

    strategy_space = SEARCH_SPACE["strategy_params"].get(strategy_class_name, {})
    for config_name, settings in strategy_space.items():
        optuna_name = settings["kwargs"]["name"]
        reverse_map[optuna_name] = ("strategy", config_name)

    rm_space = SEARCH_SPACE["risk_manager_params"].get(rm_type, {})
    for config_name, settings in rm_space.items():
        optuna_name = settings["kwargs"]["name"]
        reverse_map[optuna_name] = ("risk", config_name)

    return reverse_map

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

    strategy_class_name_for_map = AVAILABLE_STRATEGIES[args.strategy].__name__
    param_map = _build_reverse_param_map(strategy_class_name_for_map, args.rm)

    logger.info(f"--- Начало Walk-Forward Optimization ({num_steps} шагов) ---")

    for train_df, test_df, step_num in wfo_gen:
        logger.info(
            f"\n--- Шаг {step_num}/{num_steps}: Обучение на {len(train_df)} свечах, тест на {len(test_df)} свечах ---")

        study = optuna.create_study(direction="maximize")

        strategy_class = AVAILABLE_STRATEGIES[args.strategy]

        objective = Objective(
            strategy_class=strategy_class,
            exchange=args.exchange,
            instrument=args.instrument,
            interval=args.interval,
            risk_manager_type=args.rm,
            data_slice=train_df
        )
        try:
            study.optimize(objective, n_trials=args.n_trials, n_jobs=-1, show_progress_bar=True)
        except KeyboardInterrupt:
            logger.warning("\nОптимизация прервана пользователем. Завершение работы...")
            # Вежливо просим Optuna остановить все дочерние процессы
            study.stop()
            # Прерываем и внешний цикл WFO
            break

        last_study = study

        if not study.best_trial or study.best_value < 0:  # Добавлена проверка на отрицательный результат
            logger.warning(f"Шаг {step_num}: Optuna не нашла ни одного прибыльного решения. Пропускаем шаг.")
            continue

        best_params = study.best_params
        logger.info(f"Шаг {step_num}: Лучшие параметры найдены. Calmar (In-Sample): {study.best_value:.4f}")
        for key, value in best_params.items():
            logger.info(f"  - {key}: {value}")

        strategy_config_best = deepcopy(STRATEGY_CONFIG)
        risk_config_best = deepcopy(RISK_CONFIG)

        strategy_class_name = strategy_class.__name__
        for optuna_name, value in best_params.items():
            category, config_name = param_map[optuna_name]

            if category == "strategy":
                strategy_config_best[strategy_class_name][config_name] = value
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
        logger.error("Оптимизация завершена (или прервана) без единой сделки на тестовых данных. Отчеты не будут сгенерированы.")
        return


    logger.info("\n--- WFO Завершена. Генерация итоговых отчетов ---")

    # 1. Настройка путей и имен файлов
    report_dir = "optimization/reports"
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
        logger.info("--- Топ-5 лучших итераций последнего шага WFO ---")
        all_completed_trials = sorted(
            [t for t in last_study.trials if t.state == optuna.trial.TrialState.COMPLETE],
            key=lambda t: t.value,
            reverse=True
        )
        for i, trial in enumerate(all_completed_trials[:5]):
            logger.info(f"  Место #{i + 1}: Trial #{trial.number}, Calmar = {trial.value:.4f}")
            logger.info(f"    Параметры: {trial.params}")
        # ---------------------------------------------

        logger.info("Сохранение визуальных отчетов Optuna для последнего шага WFO...")
        try:
            # 1. Генерируем график как обычно
            fig_history = optuna.visualization.plot_optimization_history(last_study)

            # 2. Создаем кастомные тексты для подсказок
            hover_texts = _create_hover_text_for_trials(last_study.trials)

            # 3. ИСПРАВЛЕНИЕ: Обновляем слой графика с ТОЧКАМИ (это первый слой, data[0])
            #    Проверяем, что в графике есть хотя бы один слой
            if fig_history.data:
                fig_history.data[0].customdata = hover_texts
                fig_history.data[0].hovertemplate = (
                    "<b>Trial: %{x}</b><br>"
                    "Value: %{y}<br>"
                    "<br><b>Parameters:</b><br>"
                    "%{customdata}"
                    "<extra></extra>"  # Убираем "мусорную" информацию
                )

            # 4. Сохраняем модифицированный график
            history_path = f"{base_filename}_last_step_history.html"
            fig_history.write_html(history_path)
            logger.info(f"Успешно сохранено: {history_path}")

            # Получаем все УСПЕШНО завершенные итерации
            completed_trials = [t for t in last_study.trials if t.state == optuna.trial.TrialState.COMPLETE]

            # Логируем, сколько их на самом деле
            logger.info(f"Найдено {len(last_study.trials)} итераций всего.")
            logger.info(f"Из них {len(completed_trials)} имеют статус 'COMPLETE'.")

            # Проверяем, достаточно ли их для анализа
            if len(completed_trials) >= 2:
                logger.info("Условие (>= 2 успешных итераций) выполнено. Попытка построить график 'importance'...")

                # Строим и сохраняем второй отчет
                fig_importance = optuna.visualization.plot_param_importances(last_study)
                importance_path = f"{base_filename}_last_step_importance.html"
                fig_importance.write_html(importance_path)
                logger.info(f"Успешно сохранено: {importance_path}")
                logger.info("Оба HTML-отчета (History, Importance) успешно сохранены.")
            else:
                # Если условие не выполнено, выводим подробное предупреждение
                logger.warning("Условие НЕ выполнено. Недостаточно успешных итераций для расчета важности параметров.")
                logger.warning(
                    "Отчет 'Importance' не будет создан. Это нормально, если почти все комбинации параметров были неудачными.")
                logger.info("HTML-отчет Optuna (History) сохранен.")

        except (ImportError, ModuleNotFoundError) as e:
            logger.error(f"Критическая ошибка: библиотека Plotly не найдена, хотя она необходима. {e}")
            logger.error(
                "Пожалуйста, убедитесь, что виртуальное окружение активировано и выполните: pip install --upgrade plotly")
        except Exception as e:
            # Ловим любые другие ошибки, которые могут возникнуть при построении графика
            logger.error(f"Не удалось сохранить HTML-отчеты Optuna из-за неожиданной ошибки: {e}", exc_info=True)

        logger.info(f"Все отчеты сохранены в папку: {report_dir}")


def main():
    parser = argparse.ArgumentParser(description="Менеджер оптимизации параметров стратегий.")
    parser.add_argument("--strategy", type=str, required=True,
                        help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True, help="Тикер/символ инструмента.")
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм.")
    parser.add_argument("--rm", type=str, default="FIXED", choices=["FIXED", "ATR"])
    parser.add_argument("--n_trials", type=int, default=100, help="Количество итераций оптимизации.")
    parser.add_argument("--total_periods", type=int, required=True, help="На сколько частей делить весь датасет.")
    parser.add_argument("--train_periods", type=int, required=True, help="Сколько частей использовать для обучения.")
    parser.add_argument("--test_periods", type=int, default=1,
                        help="Сколько частей использовать для теста (по умолчанию: 1).")
    args = parser.parse_args()

    run_wfo(args)

if __name__ == "__main__":
    main()