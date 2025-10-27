import argparse
import os
import pandas as pd # Далее используется pd.DF объекты
from utils.trade_client import TinkoffTrader, IntervalType

# --- КОНФИГУРАЦИЯ ---
DATA_DIR = "data" # Папка для хранения всех данных
DAYS_TO_LOAD = 730 # Сколько дней истории загружать

def download_data(figi_list: list[str], interval_str: IntervalType):
    """
    Загружает исторические данные для указанных инструментов и интервала
    и сохраняет их в формате Parquet.
    """
    print(f"--- Загрузка данных для интервала: {interval_str} ---")

    # Создаем экземпляр нашего API-клиента.
    trader = TinkoffTrader()

    # Создаем путь к подпапке для конкретного интервала (например, "data/5min")
    interval_path = os.path.join(DATA_DIR, interval_str)
    # Создаем эту папку, если она еще не существует
    os.makedirs(interval_path, exist_ok=True)

    # Проходимся по каждому FIGI, который пользователь указал в командной строке
    for figi in figi_list:
        print(f"Загрузка {figi}...")
        # Собираем полный путь к файлу, куда будут сохранены данные
        file_path = os.path.join(interval_path, f"{figi}.parquet")

        # Вызываем метод нашего API-клиента для получения исторических данных
        df = trader.get_historical_data(
            figi=figi,
            days=DAYS_TO_LOAD,
            interval_str=interval_str
        )

        # Если данные были успешно загружены (DataFrame не пустой)
        if not df.empty:
            # Сохраняем DataFrame в файл в формате Parquet.
            df.to_parquet(file_path)
            print(f"Успешно сохранено {len(df)} свечей в файл: {file_path}")
        else:
            print(f"Не удалось загрузить данные для {figi}.")

def main():
    # Настраиваем парсер аргументов командной строки
    parser = argparse.ArgumentParser(description="Утилита для загрузки исторических данных.")

    parser.add_argument(
        "--figi",
        type=str,
        required=True,
        nargs='+',  # Позволяет указать несколько FIGI через пробел
        help="Один или несколько FIGI для загрузки (например, BBG004730N88 BBG004730RP0)."
    )
    parser.add_argument(
        "--interval",
        type=str,
        required=True,
        choices=['1min', '5min', '15min', '1hour', '1day'],
        help="Интервал свечей."
    )
    # Парсим аргументы, которые ввел пользователь
    args = parser.parse_args()
    # Запускаем основную функцию с полученными аргументами
    download_data(args.figi, args.interval)


if __name__ == "__main__":
    main()

# Пример запуска
# Загрузить 5-минутные свечи
# python download_data.py --figi BBG004730N88 BBG004730RP0 --interval 5min
#
# Загрузить дневные свечи
# python download_data.py --figi BBG004730N88 --interval 1day