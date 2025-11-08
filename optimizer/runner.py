import argparse
import optuna
import os
from datetime import datetime

from optimizer.objective import Objective
from strategies import AVAILABLE_STRATEGIES


def main():
    parser = argparse.ArgumentParser(description="Менеджер оптимизации параметров стратегий.")
    parser.add_argument("--strategy", type=str, required=True,
                        help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True, help="Тикер/символ инструмента.")
    parser.add_argument("--interval", type=str, required=True, help="Таймфрейм.")
    parser.add_argument("--rm", dest="risk_manager_type", type=str, default="FIXED", choices=["FIXED", "ATR"])
    parser.add_argument("--n_trials", type=int, default=100, help="Количество итераций оптимизации.")
    args = parser.parse_args()

    print(f"--- Запуск оптимизации для '{args.strategy}' на '{args.instrument}' ({args.n_trials} итераций) ---")

    # 1. Создаем "study" - сессию оптимизации
    # direction="maximize" - мы хотим максимизировать Calmar Ratio
    study = optuna.create_study(direction="maximize")

    # 2. Создаем экземпляр нашей целевой функции
    objective = Objective(
        strategy_name=args.strategy,
        exchange=args.exchange,
        instrument=args.instrument,
        interval=args.interval,
        risk_manager_type=args.risk_manager_type,
    )

    # 3. Запускаем процесс оптимизации
    study.optimize(objective, n_trials=args.n_trials, show_progress_bar=True)

    # 4. Выводим результаты
    print("\n--- Оптимизация завершена ---")
    print(f"Лучшее значение (Calmar Ratio): {study.best_value:.4f}")
    print("Лучшие параметры:")
    for key, value in study.best_params.items():
        print(f"  {key}: {value}")

    # 5. Сохраняем отчеты
    report_dir = "optimizer/reports"
    os.makedirs(report_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"{report_dir}/{timestamp}_{args.strategy}_{args.instrument}"

    try:
        fig_history = optuna.visualization.plot_optimization_history(study)
        fig_history.write_html(f"{base_filename}_history.html")

        fig_importance = optuna.visualization.plot_param_importances(study)
        fig_importance.write_html(f"{base_filename}_importance.html")

        print(f"\nОтчеты сохранены в {report_dir}/")
    except (ImportError, ModuleNotFoundError):
        print("\nДля сохранения отчетов установите plotly: pip install plotly")
    except Exception as e:
        print(f"\nНе удалось сохранить отчеты: {e}")

if __name__ == "__main__":
    main()