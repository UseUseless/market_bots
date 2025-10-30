import argparse
import os
from typing import get_args
import pandas as pd # Далее используется pd.DF объекты
import logging
from utils.trade_client import TinkoffTrader, IntervalType
from config import DATA_LOADER_CONFIG, PATH_CONFIG

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# Получаем доступ к логгеру библиотеки 'tinkoff' и повышаем его уровень до WARNING.
# Это скроет все информационные сообщения (INFO) от API, но оставит важные предупреждения (WARNING) и ошибки (ERROR).
# Будет высвечиваться только одна строка и на том же месте обновляться.
# Без этого каждый день скачки свечей была бы новая строка в консоли
logging.getLogger('tinkoff').setLevel(logging.WARNING)

# --- КОНФИГУРАЦИЯ ---
DATA_DIR = PATH_CONFIG["DATA_DIR"]
# Количество дней на скачку по умолчанию
DEFAULT_DAYS_TO_LOAD = DATA_LOADER_CONFIG["DAYS_TO_LOAD"]

def download_data(figi_list: list[str], interval_str: IntervalType, days_to_load: int):
    """
    Загружает исторические данные для указанных инструментов и интервала
    и сохраняет их в формате Parquet.
    """

    logging.info(f"--- Загрузка данных за {days_to_load} дней для интервала: {interval_str} ---")

    # Создаем экземпляр нашего API-клиента.
    trader = TinkoffTrader()

    # Создаем путь к подпапке для конкретного интервала (например, "data/5min")
    interval_path = os.path.join(DATA_DIR, interval_str)
    # Создаем эту папку, если она еще не существует
    os.makedirs(interval_path, exist_ok=True)

    # Проходимся по каждому FIGI, который пользователь указал в командной строке
    for figi in figi_list:
        # Собираем полный путь к файлу, куда будут сохранены данные
        file_path = os.path.join(interval_path, f"{figi}.parquet")

        # Вызываем метод нашего API-клиента для получения исторических данных
        df = trader.get_historical_data(
            figi=figi,
            days=days_to_load,
            interval_str=interval_str
        )

        # Если данные были успешно загружены (DataFrame не пустой)
        if not df.empty:
            # Сохраняем DataFrame в файл в формате Parquet.
            df.to_parquet(file_path)
            logging.info(f"Успешно сохранено {len(df)} свечей для {figi} в файл: {file_path}")

def main():
    # Настраиваем парсер аргументов командной строки
    parser = argparse.ArgumentParser(description="Утилита для загрузки исторических данных.",
                                     formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument(
        "--figi",
        type=str,
        required=True,
        nargs='+',  # Позволяет указать несколько FIGI через пробел
        help="Один или несколько FIGI для загрузки (например, BBG004730N88 BBG004730RP0)."
    )

    # get_args(IntervalType) извлекает все возможные значения из Literal
    # Это гарантирует, что choices в argparse всегда будут синхронизированы с IntervalType
    valid_intervals = get_args(IntervalType)
    parser.add_argument(
        "--interval",
        type=str,
        required=True,
        choices=valid_intervals,
        help="Интервал свечей."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS_TO_LOAD,
        help=f"Количество дней истории для загрузки (по умолчанию: {DEFAULT_DAYS_TO_LOAD})."
    )
    # Парсим аргументы, которые ввел пользователь
    args = parser.parse_args()
    # Запускаем основную функцию с полученными аргументами
    download_data(args.figi, args.interval, args.days)


if __name__ == "__main__":
    main()

# Пример запуска
# Загрузить 5-минутные свечи
# python download_data.py --figi BBG004730N88 BBG004730RP0 --interval 5min
#
# Загрузить дневные свечи
# python download_data.py --figi BBG004730N88 --interval 1day