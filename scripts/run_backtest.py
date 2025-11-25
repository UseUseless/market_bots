import argparse
import logging

# Импортируем нашу новую функцию-оркестратор
from app.engines.backtest.flows.single import run_single_backtest_flow
from app.strategies import AVAILABLE_STRATEGIES
from app.services.risk_engine.risk_manager import AVAILABLE_RISK_MANAGERS
from app.core.logging_setup import setup_global_logging

def main():
    """
    Точка входа для запуска одиночного бэктеста из командной строки.
    Эта функция только парсит аргументы и передает их в основной "flow".
    """
    # Используем глобальный логгер, так как специфичный для бэктеста
    # будет настроен внутри flow.
    setup_global_logging()

    parser = argparse.ArgumentParser(
        description="Запуск одиночного бэктеста для торговой стратегии."
    )
    # Аргументы остаются теми же
    parser.add_argument("--strategy", type=str, required=True, choices=list(AVAILABLE_STRATEGIES.keys()))
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True)
    parser.add_argument("--interval", type=str, required=True)
    parser.add_argument("--rm", dest="risk_manager_type", type=str, default="FIXED", choices=list(AVAILABLE_RISK_MANAGERS.keys()))
    args = parser.parse_args()

    # Конвертируем Namespace от argparse в словарь
    settings = vars(args)

    try:
        # Вызываем нашу централизованную функцию
        run_single_backtest_flow(settings)
    except Exception as e:
        logging.getLogger(__name__).critical(f"Критическая ошибка на верхнем уровне: {e}", exc_info=True)


if __name__ == "__main__":
    main()