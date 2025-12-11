"""
Менеджер управления рыночными данными (Data Manager).

Этот модуль содержит высокоуровневые сценарии ("флоу") для загрузки
исторических данных и обновления списков инструментов. Он связывает
клиенты бирж (Exchange Clients) с файловой системой.

Основные задачи:
1.  **update_lists_flow**: Скачивание топа ликвидных инструментов.
2.  **download_data_flow**: Массовая загрузка истории (свечи + метаданные).
    Оптимизирована через ThreadPoolExecutor для параллельной обработки.
"""

import os
import logging
import json
import concurrent.futures
from typing import Dict, Any, Tuple, List

from tqdm import tqdm

from app.shared.interfaces import ExchangeDataGetter
from app.shared.config import config

logger = logging.getLogger(__name__)


def _fetch_and_save_candles(
    client: ExchangeDataGetter,
    instrument: str,
    interval: str,
    days: int,
    category: str,
    save_path: str
) -> bool:
    """
    Запрашивает исторические свечи у клиента и сохраняет их в формате Parquet.

    Args:
        client (ExchangeDataGetter): Инициализированный клиент биржи.
        instrument (str): Тикер инструмента.
        interval (str): Временной интервал.
        days (int): Глубина истории в днях.
        category (str): Категория рынка (linear/spot).
        save_path (str): Полный путь для сохранения файла.

    Returns:
        bool: True, если данные успешно сохранены, иначе False.
    """
    try:
        # Получаем DataFrame с данными
        df = client.get_historical_data(instrument, interval, days, category=category)

        if df is not None and not df.empty:
            df.to_parquet(save_path)
            return True
        else:
            logger.warning(f"[_fetch_candles] Пустые данные для {instrument}. Файл не создан.")
            return False

    except Exception as e:
        logger.error(f"[_fetch_candles] Ошибка при загрузке {instrument}: {e}")
        return False


def _fetch_and_save_instrument_info(
    client: ExchangeDataGetter,
    instrument: str,
    category: str,
    save_path: str
) -> bool:
    """
    Запрашивает метаданные инструмента и сохраняет их в JSON.

    Args:
        client (ExchangeDataGetter): Клиент биржи.
        instrument (str): Тикер инструмента.
        category (str): Категория рынка.
        save_path (str): Полный путь для сохранения файла.

    Returns:
        bool: True, если данные успешно сохранены.
    """
    try:
        instrument_info = client.get_instrument_info(instrument, category=category)

        if instrument_info:
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(instrument_info, f, ensure_ascii=False, indent=4)
            return True
        else:
            logger.warning(f"[_fetch_info] Нет метаданных для {instrument}.")
            return False

    except Exception as e:
        logger.error(f"[_fetch_info] Ошибка при загрузке инфо {instrument}: {e}")
        return False


def _process_single_instrument_download(
    instrument: str,
    client: ExchangeDataGetter,
    exchange_path: str,
    interval: str,
    days: int,
    category: str
) -> str:
    """
    Воркер для обработки одного инструмента в отдельном потоке.

    Выполняет последовательно:
    1. Загрузку свечей.
    2. Загрузку метаданных.

    Args:
        instrument (str): Тикер инструмента.
        client (ExchangeDataGetter): Клиент биржи (должен быть потокобезопасным).
        exchange_path (str): Путь к директории биржи/интервала.
        interval (str): Интервал свечей.
        days (int): Глубина истории.
        category (str): Категория рынка.

    Returns:
        str: Строка статуса для логирования (например, "BTCUSDT: Candles, Info").
    """
    instrument_upper = instrument.upper()

    # Формируем пути
    parquet_path = os.path.join(exchange_path, f"{instrument_upper}.parquet")
    json_path = os.path.join(exchange_path, f"{instrument_upper}.json")

    # 1. Скачивание свечей
    candles_ok = _fetch_and_save_candles(
        client, instrument, interval, days, category, parquet_path
    )

    # 2. Скачивание метаданных
    info_ok = _fetch_and_save_instrument_info(
        client, instrument, category, json_path
    )

    # Формируем отчет
    status_parts = []
    if candles_ok:
        status_parts.append("Candles")
    if info_ok:
        status_parts.append("Info")

    if not status_parts:
        return f"❌ {instrument_upper}: Failed"

    return f"✅ {instrument_upper}: {', '.join(status_parts)}"


def update_lists_flow(args_settings: Dict[str, Any], client: ExchangeDataGetter) -> Tuple[bool, str]:
    """
    Сценарий обновления списка ликвидных инструментов.

    Запрашивает у биржи топ инструментов по обороту и сохраняет их в текстовый файл.

    Args:
        args_settings (Dict[str, Any]): Настройки запуска.
        client (ExchangeDataGetter): Клиент биржи.

    Returns:
        Tuple[bool, str]: (Успех, Сообщение).
    """
    exchange = args_settings["exchange"]
    logger.info(f"--- Обновление списков ликвидности: {exchange.upper()} ---")

    datalists_dir = config.DATALISTS_DIR
    os.makedirs(datalists_dir, exist_ok=True)

    expected_count = config.DATA_LOADER_CONFIG["LIQUID_INSTRUMENTS_COUNT"]

    try:
        # Запрос к бирже (может быть долгим для Tinkoff, но там теперь ThreadPool)
        tickers = client.get_top_liquid_by_turnover(count=expected_count)

        if not tickers:
            msg = f"API {exchange.upper()} вернул пустой список."
            logger.warning(msg)
            return False, msg

        filename = f"{exchange}_top_liquid_by_turnover.txt"
        file_path = os.path.join(datalists_dir, filename)

        with open(file_path, 'w', encoding='utf-8') as f:
            for ticker in tickers:
                f.write(f"{ticker}\n")

        actual_count = len(tickers)
        success_msg = (
            f"Список обновлен: {file_path}\n"
            f"Инструментов: {actual_count} / {expected_count}"
        )
        logger.info(f"Список сохранен. Тикеров: {actual_count}")
        return True, success_msg

    except Exception as e:
        error_msg = f"Ошибка обновления списка {exchange.upper()}: {e}"
        logger.error(error_msg, exc_info=True)
        return False, error_msg


def download_data_flow(args_settings: Dict[str, Any], client: ExchangeDataGetter) -> None:
    """
    Сценарий массовой загрузки исторических данных.

    Оптимизирован для скорости: использует ThreadPoolExecutor для параллельной
    загрузки нескольких инструментов одновременно.

    Ограничения:
        max_workers=3 выбрано для безопасности лимитов API (особенно Tinkoff).

    Args:
        args_settings (Dict[str, Any]): Параметры загрузки.
        client (ExchangeDataGetter): Клиент биржи.
    """
    instrument_list: List[str] = []

    # 1. Определение списка инструментов
    if args_settings.get("instrument"):
        instrument_list = args_settings["instrument"]
    elif args_settings.get("list"):
        list_path = os.path.join(config.DATALISTS_DIR, args_settings["list"])
        try:
            with open(list_path, 'r', encoding='utf-8') as f:
                instrument_list = [line.strip() for line in f if line.strip()]
            logger.info(f"Загружен список: {len(instrument_list)} тикеров из {os.path.basename(list_path)}")
        except FileNotFoundError:
            logger.error(f"Файл списка не найден: {list_path}")
            return

    if not instrument_list:
        logger.error("Список инструментов пуст.")
        return

    # 2. Подготовка параметров
    exchange = args_settings["exchange"]
    interval = args_settings["interval"]
    days = args_settings.get("days", config.DATA_LOADER_CONFIG["DAYS_TO_LOAD"])
    category = args_settings.get("category", "linear")

    logger.info(
        f"--- Старт загрузки: {exchange.upper()} | {len(instrument_list)} шт. | "
        f"{days} дней | {interval} ---"
    )

    # Создание директории: data/{exchange}/{interval}/
    data_dir = config.DATA_DIR
    exchange_path = os.path.join(data_dir, exchange, interval)
    os.makedirs(exchange_path, exist_ok=True)

    # 3. Параллельная загрузка
    # Используем max_workers=3, чтобы не получить 429 от API
    max_workers = 3

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Формируем задачи
        future_to_instr = {
            executor.submit(
                _process_single_instrument_download,
                instr, client, exchange_path, interval, days, category
            ): instr
            for instr in instrument_list
        }

        # Обрабатываем результаты по мере завершения
        # position=0 и leave=True помогают tqdm корректно работать в многопоточном окружении
        for future in tqdm(
            concurrent.futures.as_completed(future_to_instr),
            total=len(instrument_list),
            desc="Общий прогресс",
            unit="instr",
            position=0,
            leave=True
        ):
            instr = future_to_instr[future]
            try:
                result_msg = future.result()
                # Логируем результат
                logger.info(f"Result for {instr}: {result_msg}")
            except Exception as e:
                logger.error(f"Exception in worker for {instr}: {e}")

    logger.info("--- Загрузка завершена ---")