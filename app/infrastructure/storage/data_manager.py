"""
Менеджер управления рыночными данными (Data Manager).

Этот модуль содержит высокоуровневые функции ("флоу") для загрузки
исторических данных и обновления списков инструментов. Он связывает
клиенты бирж (Exchange Clients) с файловой системой.

Основные задачи:
1. Скачивание исторических свечей и сохранение их в Parquet.
2. Скачивание метаданных инструментов (шаг цены, лот) в JSON.
3. Обновление списков ликвидных инструментов для скринера.
"""

import os
import logging
import json
import time
from typing import Dict, Any, Tuple

from app.core.interfaces import BaseDataClient
from app.shared.config import config

logger = logging.getLogger(__name__)


def _fetch_and_save_candles(client: BaseDataClient, exchange: str, instrument: str, interval: str, days: int,
                            category: str, save_path: str):
    """
    Приватная функция: Запрашивает свечи у клиента и сохраняет их на диск.

    Args:
        client: Инициализированный клиент биржи.
        exchange: Название биржи (для логов).
        instrument: Тикер инструмента.
        interval: Таймфрейм.
        days: Глубина истории.
        category: Категория рынка (spot/linear).
        save_path: Полный путь к файлу .parquet.
    """
    df = client.get_historical_data(instrument, interval, days, category=category)

    if df is not None and not df.empty:
        # Сохраняем в эффективном бинарном формате Parquet (сжимает в 10-20 раз лучше CSV)
        df.to_parquet(save_path)
        logger.info(
            f"Успешно сохранено {len(df)} свечей для {instrument.upper()} в файл: {os.path.basename(save_path)}")
    else:
        logger.warning(f"Не получено данных по свечам для {instrument.upper()}. Файл не создан.")


def _fetch_and_save_instrument_info(client: BaseDataClient, instrument: str, category: str, save_path: str):
    """
    Приватная функция: Запрашивает метаданные инструмента и сохраняет в JSON.

    Args:
        client: Клиент биржи.
        instrument: Тикер.
        category: Категория рынка.
        save_path: Путь к файлу .json.
    """
    instrument_info = client.get_instrument_info(instrument, category=category)

    if instrument_info:
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(instrument_info, f, ensure_ascii=False, indent=4)
        logger.info(f"Успешно сохранена информация об инструменте в файл: {os.path.basename(save_path)}")
    else:
        logger.warning(f"Не получено метаданных для {instrument.upper()}. Файл не создан.")


def update_lists_flow(args_settings: Dict[str, Any], client: BaseDataClient) -> Tuple[bool, str]:
    """
    Сценарий обновления списка ликвидных инструментов.

    Запрашивает у биржи топ инструментов по обороту и сохраняет их в текстовый файл
    в папку `datalists`.

    Args:
        args_settings (dict): Настройки запуска (exchange, count и т.д.).
        client (BaseDataClient): Клиент биржи.

    Returns:
        Tuple[bool, str]: (Успех операции, Сообщение для пользователя).
    """
    exchange = args_settings["exchange"]
    logger.info(f"--- Запуск потока обновления списка ликвидных инструментов для биржи: {exchange.upper()} ---")

    datalists_dir = config.DATALISTS_DIR
    os.makedirs(datalists_dir, exist_ok=True)

    expected_count = config.DATA_LOADER_CONFIG["LIQUID_INSTRUMENTS_COUNT"]

    try:
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


def download_data_flow(args_settings: Dict[str, Any], client: BaseDataClient):
    """
    Сценарий массовой загрузки исторических данных.

    Читает список инструментов (из аргументов или файла), создает структуру папок
    и последовательно скачивает данные для каждого тикера.

    Args:
        args_settings (dict): Параметры (exchange, interval, list/instrument, days, category).
        client (BaseDataClient): Клиент биржи.
    """
    instrument_list = []

    # Определение источника списка инструментов
    if args_settings.get("instrument"):
        # Если передан конкретный тикер (или список тикеров) через аргументы
        instrument_list = args_settings["instrument"]
    elif args_settings.get("list"):
        # Если передано имя файла списка
        list_path = os.path.join(config.DATALISTS_DIR, args_settings["list"])
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

    exchange = args_settings["exchange"]
    interval = args_settings["interval"]
    days = args_settings.get("days", config.DATA_LOADER_CONFIG["DAYS_TO_LOAD"])
    category = args_settings.get("category", "linear")

    logger.info(
        f"--- Запуск потока загрузки данных с биржи '{exchange.upper()}' за {days} дней для интервала: {interval} ---")

    # Создание директории для хранения: data/{exchange}/{interval}/
    data_dir = config.DATA_DIR
    exchange_path = os.path.join(data_dir, exchange, interval)
    os.makedirs(exchange_path, exist_ok=True)

    for i, instrument in enumerate(instrument_list):
        logger.info(f"\n--- Скачивание {i + 1}/{len(instrument_list)}: {instrument.upper()} ---")
        instrument_upper = instrument.upper()

        parquet_path = os.path.join(exchange_path, f"{instrument_upper}.parquet")
        json_path = os.path.join(exchange_path, f"{instrument_upper}.json")

        # 1. Скачивание свечей
        _fetch_and_save_candles(client, exchange, instrument, interval, days, category, parquet_path)

        # 2. Скачивание метаданных (размер лота, шаг цены)
        _fetch_and_save_instrument_info(client, instrument, category, json_path)

        # Пауза между запросами для предотвращения бана по IP, если список большой
        if len(instrument_list) > 1:
            time.sleep(1)