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

# --- Конфигурация ---
DATA_DIR = PATH_CONFIG["DATA_DIR"]
DEFAULT_DAYS_TO_LOAD = DATA_LOADER_CONFIG["DAYS_TO_LOAD"]

def _fetch_and_save_candles(client: BaseDataClient, exchange: str, instrument: str, interval: str, days: int,
                            category: str, save_path: str):
    """Получает и сохраняет исторические свечи."""
    df = None
    if exchange == 'tinkoff':
        df = client.get_historical_data(instrument, interval, days)
    elif exchange == 'bybit':
        df = client.get_historical_data(instrument, interval, days, category=category)

    if df is not None and not df.empty:
        df.to_parquet(save_path)
        logging.info(f"Успешно сохранено {len(df)} свечей для {instrument.upper()} в файл: {save_path}")
    else:
        logging.warning(f"Не получено данных по свечам для {instrument.upper()}. Файл не создан.")

def _fetch_and_save_instrument_info(client: BaseDataClient, exchange: str, instrument: str, category: str,
                                    save_path: str):
    """Получает и сохраняет метаданные об инструменте."""
    instrument_info = None
    if exchange == 'tinkoff':
        instrument_info = client.get_instrument_info(instrument)
    elif exchange == 'bybit':
        instrument_info = client.get_instrument_info(instrument, category=category)

    if instrument_info:
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(instrument_info, f, ensure_ascii=False, indent=4)
        logging.info(f"Успешно сохранена информация об инструменте в файл: {save_path}")
    else:
        logging.warning(f"Не получено метаданных для {instrument.upper()}. Файл не создан.")

def download_data(exchange: str, instrument_list: list[str], interval: str, days_to_load: int, category: str, data_dir: str):
    """
    Загружает исторические данные, вызывая нужный клиент
    """
    logging.info(
        f"--- Загрузка данных с биржи '{exchange.upper()}' за {days_to_load} дней для интервала: {interval} ---")
    client: BaseDataClient
    if exchange == 'tinkoff':
        client = TinkoffHandler()
    elif exchange == 'bybit':
        client = BybitHandler()
    else:
        logging.error(f"Неизвестная биржа: {exchange}")
        return

    # Создаем путь к подпапке биржи и интервала
    exchange_path = os.path.join(data_dir, exchange, interval)
    os.makedirs(exchange_path, exist_ok=True)

    for i, instrument in enumerate(instrument_list):
        logging.info(f"\n--- Скачивание {i + 1}/{len(instrument_list)}: {instrument.upper()} ---")
        instrument_upper = instrument.upper()

        parquet_path = os.path.join(exchange_path, f"{instrument_upper}.parquet")
        json_path = os.path.join(exchange_path, f"{instrument_upper}.json")

        _fetch_and_save_candles(client, exchange, instrument, interval, days_to_load, category, parquet_path)
        _fetch_and_save_instrument_info(client, exchange, instrument, category, json_path)

        # Уважительная пауза, чтобы не получить бан от API
        if len(instrument_list) > 1:
            time.sleep(1)

def main():
    setup_global_logging()
    parser = argparse.ArgumentParser(
        description="Утилита для загрузки исторических данных с разных бирж.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа для загрузки данных."
    )
    parser.add_argument(
        "--instrument", type=str, required=False, nargs='+',
        help="Один или несколько тикеров/символов.\n"
             "Примеры для Tinkoff: SBER GAZP\n"
             "Примеры для Bybit: BTCUSDT ETHUSDT"
    )
    parser.add_argument(
        "--list", type=str, required=False,
        help="Имя файла из папки 'datalists' для пакетной загрузки (например, moex_blue_chips.txt)."
    )
    parser.add_argument(
        "--interval", type=str, required=True,
        help="Интервал свечей. Должен поддерживаться выбранной биржей.\n"
             "Общие для Tinkoff и Bybit: 1min, 5min, 15min, 1hour, 1day"
    )

    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS_TO_LOAD,
        help=f"Количество дней истории для загрузки (по умолчанию: {DEFAULT_DAYS_TO_LOAD})."
    )

    parser.add_argument(
        "--category", type=str, default="linear",
        help="Категория рынка для Bybit (spot, linear, inverse). По умолчанию: linear"
    )

    parser.add_argument(
        "--data_dir", type=str, default=DATA_DIR,
        help="Путь к папке для сохранения данных."
    )

    args = parser.parse_args()
    if not args.instrument and not args.list:
        parser.error("Необходимо указать либо --instrument, либо --list.")

    instrument_list = []
    if args.instrument:
        instrument_list = args.instrument
    elif args.list:
        list_path = os.path.join("datalists", args.list)
        try:
            with open(list_path, 'r', encoding='utf-8') as f:
                instrument_list = [line.strip() for line in f if line.strip()]
            logging.info(f"Загружен список из {len(instrument_list)} инструментов из файла: {list_path}")
        except FileNotFoundError:
            parser.error(f"Файл со списком не найден: {list_path}")

    download_data(args.exchange, instrument_list, args.interval, args.days, args.category, args.data_dir)


if __name__ == "__main__":
    main()