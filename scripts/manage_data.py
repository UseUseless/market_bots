"""
CLI-скрипт для управления рыночными данными (Data Manager).

Предоставляет интерфейс командной строки для двух основных операций:
1. `update`: Обновление (создание) списков ликвидных инструментов (скачивает топ по обороту)
    и сохраняет их в текстовые файлы в папке `datalists/.
2. `download`: Загрузка исторических свечей (OHLCV) и метаданных инструментов.

Скрипт инициализирует необходимые зависимости (клиенты бирж)
через DI-контейнер и передает управление функциям-оркестраторам в `app.infrastructure`.

Примеры запуска:
    Обновить список ликвидных монет для Bybit:
    $ python scripts/manage_data.py update --exchange bybit

    Скачать историю за год для тикеров из файла:
    $ python scripts/manage_data.py download --exchange tinkoff --list tinkoff_top_liquid.txt --interval 1hour

    Скачать конкретные тикеры:
    $ python scripts/manage_data.py download --exchange bybit --instrument BTCUSDT ETHUSDT --interval 15min
"""

import argparse
import logging
import sys
import os
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.storage.data_manager import update_lists_flow, download_data_flow
from app.bootstrap.container import container
from app.shared.primitives import ExchangeType
from app.shared.config import config
from app.core.interfaces import BaseDataClient
from app.shared.logging_setup import setup_global_logging
from app.shared.decorators import safe_entry

DEFAULT_DAYS_TO_LOAD = config.DATA_LOADER_CONFIG["DAYS_TO_LOAD"]


def _get_client(args_settings: Dict[str, Any]) -> BaseDataClient:
    """
    Создает клиент биржи.

    Args:
        args_settings (Dict[str, Any]): Словарь аргументов, полученный из argparse.
            Должен содержать ключ 'exchange'.

    Returns:
        BaseDataClient: Инициализированный клиент биржи (TinkoffHandler или BybitHandler),
        полученный из DI-контейнера.
    """
    exchange = args_settings.get("exchange")
    mode = "SANDBOX" if exchange == ExchangeType.TINKOFF else "REAL"
    return container.get_exchange_client(exchange, mode=mode)

@safe_entry
def main() -> None:
    """
    Основная функция-диспетчер.

    Алгоритм работы:
    1. Настраивает tqdm логирование
    2. Парсит аргументы командной строки (argparse).
    3. Создает соответствующий клиент биржи через `_get_client`.
    4. Вызывает соответствующую функцию бизнес-логики (`update_lists_flow` или `download_data_flow`),
       передавая ей настройки и готовый клиент.
    """
    # Включаем режим tqdm, чтобы логи не ломали прогресс-бар загрузки
    setup_global_logging(mode='tqdm', log_level=logging.INFO)

    parser = argparse.ArgumentParser(
        description="Утилита для управления рыночными данными.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    # dest='command' означает, что имя выбранной команды (update или download) запишется в переменную args.command
    subparsers = parser.add_subparsers(dest='command', required=True, help='Доступные команды')

    # --- 1. Настройка команды 'update' ---
    parser_update = subparsers.add_parser(
        'update',
        help='Обновить список ликвидных инструментов.'
    )
    parser_update.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа."
    )

    # --- 2. Настройка команды 'download' ---
    parser_download = subparsers.add_parser(
        'download',
        help='Скачать исторические данные.'
    )
    parser_download.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа."
    )

    group = parser_download.add_mutually_exclusive_group(required=True)
    group.add_argument("--instrument", type=str, nargs='+', help="Тикеры через пробел.")
    group.add_argument("--list", type=str, help="Имя файла списка.")

    parser_download.add_argument("--interval", type=str, required=True, help="Интервал.")
    parser_download.add_argument("--days", type=int, default=DEFAULT_DAYS_TO_LOAD, help="Дни.")
    parser_download.add_argument("--category", type=str, default="linear", help="Категория Bybit.")

    # --- Парсинг и выбор действия ---
    args = parser.parse_args()
    args_settings = vars(args)

    # Получаем клиент из DI-контейнера
    client = _get_client(args_settings)
    command = args_settings.get('command')

    if command == 'update':
        update_lists_flow(args_settings, client)
    elif command == 'download':
        download_data_flow(args_settings, client)


if __name__ == "__main__":
    main()