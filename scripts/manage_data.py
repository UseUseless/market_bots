import argparse
import os
import logging
import json
import time

from app.utils.logging_setup import setup_global_logging
from app.utils.clients.abc import BaseDataClient
from app.utils.clients.tinkoff import TinkoffHandler
from app.utils.clients.bybit import BybitHandler
from config import DATA_LOADER_CONFIG, PATH_CONFIG

DATA_DIR = PATH_CONFIG["DATA_DIR"]
DATALISTS_DIR = PATH_CONFIG["DATALISTS_DIR"]
DEFAULT_DAYS_TO_LOAD = DATA_LOADER_CONFIG["DAYS_TO_LOAD"]
LIQUID_INSTRUMENTS_COUNT = DATA_LOADER_CONFIG["LIQUID_INSTRUMENTS_COUNT"]


def _get_client_for_update(exchange: str) -> BaseDataClient:
    """Создает клиент для публичных запросов (получения списков)."""
    if exchange == 'tinkoff':
        return TinkoffHandler()
    elif exchange == 'bybit':
        # Для получения публичных данных (списки, тикеры) можно использовать REAL режим без ключей
        return BybitHandler(trade_mode="REAL")
    else:
        raise ValueError(f"Неизвестная биржа: {exchange}")


def update_lists(args: argparse.Namespace):
    """
    Основная функция для команды 'update'.
    Обновляет список топ-N ликвидных инструментов и сохраняет в файл.
    """
    logging.info(f"--- Обновление списка ликвидных инструментов для биржи: {args.exchange.upper()} ---")
    os.makedirs(DATALISTS_DIR, exist_ok=True)

    try:
        client = _get_client_for_update(args.exchange)
        tickers = client.get_top_liquid_by_turnover(count=LIQUID_INSTRUMENTS_COUNT)

        if not tickers:
            logging.warning("Получен пустой список тикеров. Файл не будет обновлен.")
            return

        filename = f"{args.exchange}_top_liquid_by_turnover.txt"
        file_path = os.path.join(DATALISTS_DIR, filename)

        with open(file_path, 'w', encoding='utf-8') as f:
            for ticker in tickers:
                f.write(f"{ticker}\n")

        logging.info(f"Список успешно сохранен в файл: {file_path}. Всего {len(tickers)} тикеров.")

    except Exception as e:
        logging.error(f"Произошла ошибка при обновлении списка: {e}", exc_info=True)

def _get_client_for_download(exchange: str) -> BaseDataClient:
    """Создает клиент для скачивания данных."""
    if exchange == 'tinkoff':
        return TinkoffHandler()
    elif exchange == 'bybit':
        return BybitHandler()  # По умолчанию SANDBOX, но для данных это не важно
    else:
        raise ValueError(f"Неизвестная биржа: {exchange}")


def _fetch_and_save_candles(client: BaseDataClient, exchange: str, instrument: str, interval: str, days: int,
                            category: str, save_path: str):
    """Получает и сохраняет исторические свечи."""
    df = client.get_historical_data(instrument, interval, days, category=category)

    if df is not None and not df.empty:
        df.to_parquet(save_path)
        logging.info(
            f"Успешно сохранено {len(df)} свечей для {instrument.upper()} в файл: {os.path.basename(save_path)}")
    else:
        logging.warning(f"Не получено данных по свечам для {instrument.upper()}. Файл не создан.")


def _fetch_and_save_instrument_info(client: BaseDataClient, exchange: str, instrument: str, category: str,
                                    save_path: str):
    """Получает и сохраняет метаданные об инструменте."""
    instrument_info = client.get_instrument_info(instrument, category=category)

    if instrument_info:
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(instrument_info, f, ensure_ascii=False, indent=4)
        logging.info(f"Успешно сохранена информация об инструменте в файл: {os.path.basename(save_path)}")
    else:
        logging.warning(f"Не получено метаданных для {instrument.upper()}. Файл не создан.")


def download_data(args: argparse.Namespace):
    """
    Основная функция для команды 'download'.
    Загружает исторические данные и метаданные для списка инструментов.
    """
    instrument_list = []
    if args.instrument:
        instrument_list = args.instrument
    elif args.list:
        list_path = os.path.join(DATALISTS_DIR, args.list)
        try:
            with open(list_path, 'r', encoding='utf-8') as f:
                instrument_list = [line.strip() for line in f if line.strip()]
            logging.info(f"Загружен список из {len(instrument_list)} инструментов из файла: {list_path}")
        except FileNotFoundError:
            logging.error(f"Файл со списком не найден: {list_path}")
            return

    if not instrument_list:
        logging.error("Список инструментов для скачивания пуст.")
        return

    logging.info(
        f"--- Загрузка данных с биржи '{args.exchange.upper()}' за {args.days} дней для интервала: {args.interval} ---")

    client = _get_client_for_download(args.exchange)

    exchange_path = os.path.join(DATA_DIR, args.exchange, args.interval)
    os.makedirs(exchange_path, exist_ok=True)

    for i, instrument in enumerate(instrument_list):
        logging.info(f"\n--- Скачивание {i + 1}/{len(instrument_list)}: {instrument.upper()} ---")
        instrument_upper = instrument.upper()

        parquet_path = os.path.join(exchange_path, f"{instrument_upper}.parquet")
        json_path = os.path.join(exchange_path, f"{instrument_upper}.json")

        _fetch_and_save_candles(client, args.exchange, instrument, args.interval, args.days, args.category,
                                parquet_path)
        _fetch_and_save_instrument_info(client, args.exchange, instrument, args.category, json_path)

        if len(instrument_list) > 1:
            time.sleep(1)  # Уважительная пауза для API

def main():
    """
    Главная точка входа. Настраивает парсер аргументов с подкомандами.
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
    parser_update.set_defaults(func=update_lists)

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
    parser_download.set_defaults(func=download_data)

    # --- Выполнение ---
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()