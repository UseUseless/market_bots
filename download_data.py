import argparse
import os
import logging
from typing import get_args
import json

# -> ИЗМЕНЕНИЕ: Импортируем наших новых клиентов
from utils.data_clients import TinkoffClient, BybitClient, BaseDataClient
from config import DATA_LOADER_CONFIG, PATH_CONFIG

# Настройка логгеров
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger('tinkoff').setLevel(logging.WARNING)

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

    # -> ИЗМЕНЕНИЕ: Фабрика клиентов
    client: BaseDataClient
    if exchange == 'tinkoff':
        client = TinkoffClient()
    elif exchange == 'bybit':
        client = BybitClient()
    else:
        logging.error(f"Неизвестная биржа: {exchange}")
        return

    # Создаем путь к подпапке биржи и интервала
    exchange_path = os.path.join(data_dir, exchange, interval)
    os.makedirs(exchange_path, exist_ok=True)

    for instrument in instrument_list:
        instrument_upper = instrument.upper()

        # Определяем пути для сохранения в новой структуре
        parquet_path = os.path.join(exchange_path, f"{instrument_upper}.parquet")
        json_path = os.path.join(exchange_path, f"{instrument_upper}.json")

        # Вызываем helper-функции
        _fetch_and_save_candles(client, exchange, instrument, interval, days_to_load, category, parquet_path)
        _fetch_and_save_instrument_info(client, exchange, instrument, category, json_path)


def main():
    parser = argparse.ArgumentParser(
        description="Утилита для загрузки исторических данных с разных бирж.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument(
        "--exchange", type=str, required=True, choices=['tinkoff', 'bybit'],
        help="Биржа для загрузки данных."
    )
    parser.add_argument(
        "--instrument", type=str, required=True, nargs='+',
        help="Один или несколько тикеров/символов.\n"
             "Примеры для Tinkoff: SBER GAZP\n"
             "Примеры для Bybit: BTCUSDT ETHUSDT"
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
    download_data(args.exchange, args.instrument, args.interval, args.days, args.category, args.data_dir)


if __name__ == "__main__":
    main()

# Пример запуска
# Скачать данные по акции Сбербанка с Tinkoff (используя тикер):
# python download_data.py --exchange tinkoff --instrument SBER --interval 5min --days 365
# Скачать данные по BTC/USDT с Bybit:
# python download_data.py --exchange bybit --instrument BTCUSDT --interval 1hour --days 700
# Скачать данные сразу по нескольким криптовалютам:
# python download_data.py --exchange bybit --instrument BTCUSDT ETHUSDT --interval 1day --days 1000