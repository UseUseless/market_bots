import csv
import os
from datetime import datetime
import logging

HEADERS = [
    'timestamp_utc', 'strategy_name', 'figi', 'direction', 
    'entry_price', 'exit_price', 'pnl', 'exit_reason'
]

def log_trade(trade_log_file: str, strategy_name: str, figi: str, direction: str, entry_price: float, exit_price: float, pnl: float, exit_reason: str):
    """
    Записывает информацию о завершенной сделке в указанный CSV-файл.
    """
    try:
        # Убедимся, что папка для логов существует
        os.makedirs(os.path.dirname(trade_log_file), exist_ok=True)
        file_exists = os.path.isfile(trade_log_file)
        
        row_data = {
            'timestamp_utc': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'strategy_name': strategy_name,
            'figi': figi,
            'direction': direction,
            'entry_price': round(entry_price, 4),
            'exit_price': round(exit_price, 4),
            'pnl': round(pnl, 4),
            'exit_reason': exit_reason
        }

        with open(trade_log_file, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=HEADERS)
            if not file_exists:
                writer.writeheader()  # Записываем заголовок, если файл новый
            writer.writerow(row_data)
            
    except IOError as e:
        logging.error(f"Не удалось записать сделку в файл {trade_log_file}: {e}")
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при записи лога сделки: {e}")