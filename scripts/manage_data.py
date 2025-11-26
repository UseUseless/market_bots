import argparse
import logging

from app.infrastructure.storage.data_manager import update_lists_flow, download_data_flow
from app.shared.logging_setup import setup_global_logging
from app.bootstrap.container import container
from app.shared.primitives import ExchangeType
from app.shared.config import config

DEFAULT_DAYS_TO_LOAD = config.DATA_LOADER_CONFIG["DAYS_TO_LOAD"]

def main():
    """
    Точка входа для утилиты управления данными из командной строки.

    Эта функция больше не содержит бизнес-логики. Ее задачи:
    - Настроить глобальное логирование.
    - Определить и распарсить аргументы командной строки.
    - Преобразовать аргументы в словарь настроек.
    - Вызвать соответствующую функцию-"flow" из `app`, передав ей настройки.
    """
    setup_global_logging()
    parser = argparse.ArgumentParser(
        description="Утилита для управления рыночными данными: обновление списков и скачивание истории.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', required=True, help='Доступные команды')

    # --- Парсер для команды 'update' ---
    parser_update = subparsers.add_parser('update', help='Обновить и сохранить список ликвидных инструментов.')
    parser_update.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа, для которой обновляется список."
    )
    # 3. Привязываем команду 'update' к функции-оркестратору `update_lists_flow`
    parser_update.set_defaults(func=update_lists_flow)

    # --- Парсер для команды 'download' ---
    parser_download = subparsers.add_parser('download', help='Скачать исторические данные по инструментам.')
    parser_download.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа для загрузки данных."
    )
    group = parser_download.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--instrument", type=str, nargs='+',
        help="Один или несколько тикеров/символов (например, SBER GAZP)."
    )
    group.add_argument(
        "--list", type=str,
        help="Имя файла из папки 'datalists' для пакетной загрузки."
    )
    parser_download.add_argument(
        "--interval", type=str, required=True,
        help="Интервал свечей (например, 1min, 5min, 1hour, 1day)."
    )
    parser_download.add_argument(
        "--days", type=int, default=DEFAULT_DAYS_TO_LOAD,
        help=f"Количество дней истории для загрузки (по умолчанию: {DEFAULT_DAYS_TO_LOAD})."
    )
    parser_download.add_argument(
        "--category", type=str, default="linear",
        help="Категория рынка для Bybit (spot, linear, inverse). По умолчанию: linear."
    )
    # 4. Привязываем команду 'download' к функции-оркестратору `download_data_flow`
    parser_download.set_defaults(func=download_data_flow)

    # --- Выполнение ---
    args = parser.parse_args()

    # Конвертируем Namespace от argparse в обычный словарь
    args_settings = vars(args)

    # --- НОВАЯ ЛОГИКА СБОРКИ ---

    # 1. Определяем, какой клиент нужен, прямо здесь
    exchange = args_settings.get("exchange")

    # Логика выбора режима (Tinkoff=SANDBOX / Bybit=REAL) переехала сюда
    # Это делает скрипт более явным в своих намерениях
    mode = "SANDBOX" if exchange == ExchangeType.TINKOFF else "REAL"

    try:
        # 2. Достаем клиента из контейнера
        client = container.get_exchange_client(exchange, mode=mode)

        command = args_settings.get('command')

        if command == 'update':
            update_lists_flow(args_settings, client)
        elif command == 'download':
            download_data_flow(args_settings, client)

    except Exception as e:
        logging.getLogger(__name__).critical(
            f"Критическая ошибка: {e}", exc_info=True)


if __name__ == "__main__":
    main()