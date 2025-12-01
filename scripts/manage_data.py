"""
CLI-утилита для управления рыночными данными (Data Manager).

Предоставляет интерфейс командной строки для двух основных операций:
1. `update`: Обновление списков ликвидных инструментов (скачивает топ по обороту).
2. `download`: Загрузка исторических свечей (OHLCV) и метаданных инструментов.

Скрипт выступает точкой входа, инициализирует необходимые зависимости (клиенты бирж)
через DI-контейнер и передает управление функциям-оркестраторам в `app.infrastructure`.

Запуск:
    python scripts/manage_data.py update --exchange bybit
    python scripts/manage_data.py download --exchange tinkoff --list tinkoff_top_liquid.txt --interval 1hour
"""

import argparse
import logging
import sys

# Добавляем корневую директорию в sys.path, если скрипт запускается не как модуль
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.storage.data_manager import update_lists_flow, download_data_flow
from app.shared.logging_setup import setup_global_logging
from app.bootstrap.container import container
from app.shared.primitives import ExchangeType
from app.shared.config import config

DEFAULT_DAYS_TO_LOAD = config.DATA_LOADER_CONFIG["DAYS_TO_LOAD"]


def main() -> None:
    """
    Основная функция-диспетчер.

    Алгоритм работы:
    1. Настраивает глобальное логирование.
    2. Парсит аргументы командной строки (argparse).
    3. Инициализирует клиент биржи (Tinkoff/Bybit) через Container.
       - Для Tinkoff принудительно используется режим SANDBOX (безопасность).
       - Для Bybit используется режим REAL (доступ к публичным данным без ключей).
    4. Вызывает соответствующую функцию бизнес-логики (`update_lists_flow` или `download_data_flow`),
       передавая ей настройки и готовый клиент.
    """
    setup_global_logging()

    parser = argparse.ArgumentParser(
        description="Утилита для управления рыночными данными: обновление списков и скачивание истории.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', required=True, help='Доступные команды')

    # --- Команда 'update' ---
    parser_update = subparsers.add_parser(
        'update',
        help='Обновить и сохранить список ликвидных инструментов (Top Liquid).'
    )
    parser_update.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа, для которой обновляется список."
    )

    # --- Команда 'download' ---
    parser_download = subparsers.add_parser(
        'download',
        help='Скачать исторические данные по инструментам.'
    )
    parser_download.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа для загрузки данных."
    )

    # Группа взаимоисключающих аргументов: либо конкретные тикеры, либо файл со списком
    group = parser_download.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--instrument", type=str, nargs='+',
        help="Один или несколько тикеров через пробел (например: SBER GAZP BTCUSDT)."
    )
    group.add_argument(
        "--list", type=str,
        help="Имя файла из папки 'datalists' (например: tinkoff_top_liquid.txt)."
    )

    parser_download.add_argument(
        "--interval", type=str, required=True,
        help="Интервал свечей (например: 1min, 5min, 1hour, 1day)."
    )
    parser_download.add_argument(
        "--days", type=int, default=DEFAULT_DAYS_TO_LOAD,
        help=f"Глубина истории в днях (по умолчанию: {DEFAULT_DAYS_TO_LOAD})."
    )
    parser_download.add_argument(
        "--category", type=str, default="linear",
        help="Категория рынка для Bybit (linear/spot/inverse). По умолчанию: linear."
    )

    # --- Парсинг и Выполнение ---
    args = parser.parse_args()
    args_settings = vars(args)
    exchange = args_settings.get("exchange")

    # Логика выбора режима клиента для скачивания данных:
    # Tinkoff: Используем SANDBOX, так как ReadOnly токен часто имеет доступ к песочнице,
    #          а для скачивания истории этого достаточно.
    # Bybit: Используем REAL, чтобы делать публичные запросы к основному API без ключей.
    mode = "SANDBOX" if exchange == ExchangeType.TINKOFF else "REAL"

    try:
        # Получаем клиент из DI-контейнера
        client = container.get_exchange_client(exchange, mode=mode)
        command = args_settings.get('command')

        if command == 'update':
            update_lists_flow(args_settings, client)
        elif command == 'download':
            download_data_flow(args_settings, client)

    except Exception as e:
        logging.getLogger(__name__).critical(f"Критическая ошибка выполнения: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОперация прервана пользователем.")
        sys.exit(0)