import argparse
import os
import logging
from typing import List

from app.utils.logging_setup import setup_global_logging
from app.utils.clients.tinkoff import TinkoffClient
from app.utils.clients.bybit import BybitClient
from config import DATA_LOADER_CONFIG

LISTS_DIR = "datalists"

def _update_top_liquid_by_turnover(exchange: str, count: int) -> List[str]:
    """Получает список топ-N ликвидных инструментов для указанной биржи."""
    client: TinkoffClient | BybitClient
    if exchange == 'tinkoff':
        client = TinkoffClient()
    elif exchange == 'bybit':
        client = BybitClient()
    else:
        raise ValueError(f"Неизвестная биржа: {exchange}")

    return client.get_top_liquid_by_turnover(count=count)

LIST_UPDATERS = {
    "top_liquid_by_turnover": _update_top_liquid_by_turnover,
    # В будущем можно будет легко добавить новые типы:
    # "blue_chips": _update_blue_chips,
}

def update_and_save_list(exchange: str, list_type: str):
    """
    Вызывает нужный обработчик для обновления списка и сохраняет результат в файл.
    """
    logging.info(f"--- Обновление списка '{list_type}' для биржи: {exchange.upper()} ---")

    os.makedirs(LISTS_DIR, exist_ok=True)

    # Выбираем нужную функцию-обработчик из словаря
    updater_func = LIST_UPDATERS.get(list_type)
    if not updater_func:
        logging.error(f"Неизвестный тип списка: '{list_type}'. Доступные типы: {list(LIST_UPDATERS.keys())}")
        return

    try:
        count = DATA_LOADER_CONFIG['LIQUID_INSTRUMENTS_COUNT']

        tickers = updater_func(exchange, count)

        if not tickers:
            logging.warning("Получен пустой список тикеров. Файл не будет обновлен.")
            return

        filename = f"{exchange}_{list_type}.txt"
        file_path = os.path.join(LISTS_DIR, filename)

        with open(file_path, 'w', encoding='utf-8') as f:
            for ticker in tickers:
                f.write(f"{ticker}\n")

        logging.info(f"Список успешно сохранен в файл: {file_path}. Всего {len(tickers)} тикеров.")

    except Exception as e:
        logging.error(f"Произошла ошибка при обновлении списка: {e}", exc_info=True)


def main():
    setup_global_logging()
    parser = argparse.ArgumentParser(description="Утилита для обновления списков инструментов.")
    parser.add_argument(
        "--exchange",
        type=str,
        required=True,
        choices=['tinkoff', 'bybit'],
        help="Биржа для обновления списка."
    )
    parser.add_argument(
        "--list-type",
        type=str,
        default="top_liquid_by_turnover",
        choices=LIST_UPDATERS.keys(),
        help="Тип списка для обновления."
    )
    args = parser.parse_args()
    update_and_save_list(args.exchange, args.list_type)

if __name__ == "__main__":
    main()