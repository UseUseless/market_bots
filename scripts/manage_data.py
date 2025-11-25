# market_bots/scripts/manage_data.py

import argparse
import logging

# 1. Импортируем "flows" - функции, содержащие реальную логику из `app`.
from app.services.data_provider.flows import update_lists_flow, download_data_flow
from app.core.logging_setup import setup_global_logging
from config import DATA_LOADER_CONFIG

# 2. Константы для значений по умолчанию в argparse остаются здесь.
DEFAULT_DAYS_TO_LOAD = DATA_LOADER_CONFIG["DAYS_TO_LOAD"]


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
    settings = vars(args)

    # Вызываем привязанную функцию (либо update_lists_flow, либо download_data_flow)
    # и передаем ей словарь с настройками.
    try:
        settings['func'](settings)
    except Exception as e:
        logging.getLogger(__name__).critical(
            f"Произошла критическая ошибка при выполнении команды '{settings.get('command')}': {e}", exc_info=True)


if __name__ == "__main__":
    main()