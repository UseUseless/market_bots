"""
Модуль-оркестратор для всех операций по управлению данными.

Этот "flow" отвечает за:
1. Обновление списков ликвидных инструментов.
2. Скачивание исторических свечей и метаданных по инструментам.

Функции в этом модуле являются точками входа для UI (лаунчера) и
скриптов командной строки, обеспечивая единую, централизованную логику.
"""

import os
import logging
import json
import time
from typing import Dict, Any, Tuple

from app.core.interfaces.exchange_client import BaseDataClient
from app.adapters.exchanges.tinkoff import TinkoffHandler
from app.adapters.exchanges.bybit import BybitHandler
from app.core.constants import ExchangeType
from config import DATA_LOADER_CONFIG, PATH_CONFIG

# Получаем логгер для текущего модуля
logger = logging.getLogger(__name__)


# --- Вспомогательные (приватные) функции, используемые внутри этого модуля ---

def _get_client(exchange: str) -> BaseDataClient:
    """Создает и возвращает экземпляр клиента для указанной биржи."""
    if exchange == ExchangeType.TINKOFF:
        # Для публичных данных и скачивания достаточно токена "только для чтения",
        # который используется по умолчанию в конструкторе.
        return TinkoffHandler()
    elif exchange == ExchangeType.BYBIT:
        # Для публичных данных (списки, тикеры, история) можно использовать
        # режим REAL без ключей API.
        return BybitHandler(trade_mode="REAL")
    else:
        raise ValueError(f"Неизвестная биржа: {exchange}")


def _fetch_and_save_candles(client: BaseDataClient, exchange: str, instrument: str, interval: str, days: int,
                            category: str, save_path: str):
    """Получает и сохраняет исторические свечи в формате Parquet."""
    df = client.get_historical_data(instrument, interval, days, category=category)
    if df is not None and not df.empty:
        df.to_parquet(save_path)
        logger.info(
            f"Успешно сохранено {len(df)} свечей для {instrument.upper()} в файл: {os.path.basename(save_path)}")
    else:
        logger.warning(f"Не получено данных по свечам для {instrument.upper()}. Файл не создан.")


def _fetch_and_save_instrument_info(client: BaseDataClient, instrument: str, category: str, save_path: str):
    """Получает и сохраняет метаданные об инструменте в формате JSON."""
    instrument_info = client.get_instrument_info(instrument, category=category)
    if instrument_info:
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(instrument_info, f, ensure_ascii=False, indent=4)
        logger.info(f"Успешно сохранена информация об инструменте в файл: {os.path.basename(save_path)}")
    else:
        logger.warning(f"Не получено метаданных для {instrument.upper()}. Файл не создан.")

def update_lists_flow(settings: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Основная функция для команды 'update'.
    Обновляет список топ-N ликвидных инструментов и возвращает результат.

    :param settings: Словарь настроек. Ожидаемый ключ: 'exchange'.
    :return: Кортеж (успех: bool, сообщение: str).
    """
    exchange = settings["exchange"]
    logger.info(f"--- Запуск потока обновления списка ликвидных инструментов для биржи: {exchange.upper()} ---")

    datalists_dir = PATH_CONFIG["DATALISTS_DIR"]
    os.makedirs(datalists_dir, exist_ok=True)
    expected_count = DATA_LOADER_CONFIG["LIQUID_INSTRUMENTS_COUNT"]

    try:
        client = _get_client(exchange)
        tickers = client.get_top_liquid_by_turnover(count=expected_count)

        if not tickers:
            message = f"API биржи {exchange.upper()} не вернул список ликвидных инструментов. Файл не был создан."
            logger.warning(message)
            return False, message

        filename = f"{exchange}_top_liquid_by_turnover.txt"
        file_path = os.path.join(datalists_dir, filename)

        with open(file_path, 'w', encoding='utf-8') as f:
            for ticker in tickers:
                f.write(f"{ticker}\n")

        actual_count = len(tickers)
        message = (
            f"Список ликвидных инструментов для {exchange.upper()} успешно обновлен.\n"
            f"Файл сохранен в: {file_path}\n"
            f"Найдено {actual_count} из {expected_count} запрошенных инструментов."
        )
        logger.info(f"Список успешно сохранен. Найдено {actual_count} тикеров.")
        return True, message

    except Exception as e:
        message = f"Произошла ошибка при обновлении списка для {exchange.upper()}: {e}"
        logger.error(message, exc_info=True)
        return False, message


def download_data_flow(settings: Dict[str, Any]):
    """
    Основная функция для команды 'download'.
    Загружает исторические данные и метаданные для списка инструментов.

    :param settings: Словарь настроек. Ожидаемые ключи:
                     'exchange', 'interval', 'days', 'category' (опционально),
                     и либо 'instrument' (list), либо 'list' (str).
    """
    instrument_list = []
    # Определяем список инструментов для скачивания
    if settings.get("instrument"):
        instrument_list = settings["instrument"]
    elif settings.get("list"):
        list_path = os.path.join(PATH_CONFIG["DATALISTS_DIR"], settings["list"])
        try:
            with open(list_path, 'r', encoding='utf-8') as f:
                instrument_list = [line.strip() for line in f if line.strip()]
            logger.info(f"Загружен список из {len(instrument_list)} инструментов из файла: {list_path}")
        except FileNotFoundError:
            logger.error(f"Файл со списком не найден: {list_path}")
            return

    if not instrument_list:
        logger.error("Список инструментов для скачивания пуст.")
        return

    exchange = settings["exchange"]
    interval = settings["interval"]
    days = settings.get("days", DATA_LOADER_CONFIG["DAYS_TO_LOAD"])
    category = settings.get("category", "linear")  # По умолчанию 'linear' для Bybit

    logger.info(
        f"--- Запуск потока загрузки данных с биржи '{exchange.upper()}' за {days} дней для интервала: {interval} ---")

    client = _get_client(exchange)

    data_dir = PATH_CONFIG["DATA_DIR"]
    exchange_path = os.path.join(data_dir, exchange, interval)
    os.makedirs(exchange_path, exist_ok=True)

    for i, instrument in enumerate(instrument_list):
        logger.info(f"\n--- Скачивание {i + 1}/{len(instrument_list)}: {instrument.upper()} ---")
        instrument_upper = instrument.upper()

        parquet_path = os.path.join(exchange_path, f"{instrument_upper}.parquet")
        json_path = os.path.join(exchange_path, f"{instrument_upper}.json")

        _fetch_and_save_candles(client, exchange, instrument, interval, days, category, parquet_path)
        _fetch_and_save_instrument_info(client, instrument, category, json_path)

        if len(instrument_list) > 1:
            time.sleep(1)  # Уважительная пауза для API, чтобы избежать бана