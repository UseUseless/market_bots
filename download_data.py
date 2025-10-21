import argparse
import os
import pandas as pd
from utils.trade_client import TinkoffTrader

# --- КОНФИГУРАЦИЯ ---
DATA_DIR = "data" # Папка для хранения всех данных
DAYS_TO_LOAD = 730 # Сколько дней истории загружать (2 года)

def download_data(figi_list: list[str], interval_str: str):
    """
    Загружает исторические данные для указанных инструментов и интервала
    и сохраняет их в формате Parquet.
    """
    print(f"--- Загрузка данных для интервала: {interval_str} ---")
    
    # Используем любой токен, т.к. загрузка данных не требует реального счета
    trader = TinkoffTrader(trade_mode="sandbox")
    
    interval_path = os.path.join(DATA_DIR, interval_str)
    os.makedirs(interval_path, exist_ok=True)
    
    for figi in figi_list:
        print(f"Загрузка {figi}...")
        file_path = os.path.join(interval_path, f"{figi}.parquet")
        
        df = trader.get_historical_data(
            figi=figi,
            days=DAYS_TO_LOAD,
            interval_str=interval_str
        )
        
        if not df.empty:
            df.to_parquet(file_path)
            print(f"Успешно сохранено {len(df)} свечей в файл: {file_path}")
        else:
            print(f"Не удалось загрузить данные для {figi}.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Утилита для загрузки исторических данных.")
    
    parser.add_argument(
        "--figi",
        type=str,
        required=True,
        nargs='+', # Позволяет указать несколько FIGI через пробел
        help="Один или несколько FIGI для загрузки (например, BBG004730N88 BBG004730RP0)."
    )
    parser.add_argument(
        "--interval",
        type=str,
        required=True,
        choices=['1min', '5min', '15min', '1hour', '1day'],
        help="Интервал свечей."
    )
    
    args = parser.parse_args()
    download_data(args.figi, args.interval)
    
'''
Пример запуска
# Загрузить 5-минутные свечи
python download_data.py --figi BBG004730N88 BBG004730RP0 --interval 5min

# Загрузить дневные свечи
python download_data.py --figi BBG004730N88 --interval 1day
'''