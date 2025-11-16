import json
import os
import logging
from datetime import datetime

import pandas as pd
from typing import Dict, Any

from config import PATH_CONFIG

def load_trades_from_file(file_path: str) -> pd.DataFrame:
    """Загружает сделки из файла, поддерживая .jsonl формат."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл с логами сделок не найден: {file_path}")

    if file_path.endswith('.jsonl'):
        return pd.read_json(file_path, lines=True)
    else:
        raise ValueError("Неподдерживаемый формат файла логов. Используйте .jsonl")


def load_instrument_info(exchange: str, instrument: str, interval: str, data_dir: str = PATH_CONFIG["DATA_DIR"]) -> Dict[str, Any]:
    """
    Загружает метаданные об инструменте из .json файла.
    Возвращает словарь с правилами или значения по умолчанию.
    """
    file_path = os.path.join(data_dir, exchange, interval, f"{instrument.upper()}.json")
    logging.info(f"FileIO: Чтение метаданных из {file_path}...")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            logging.info(f"Метаданные для {instrument.upper()} успешно загружены.")
            return data
    except FileNotFoundError:
        logging.warning(f"Файл с метаданными не найден: {file_path}")
        logging.warning("Запуск с параметрами по умолчанию (lot=1, step=1).")
        return {"lot_size": 1, "qty_step": 1.0, "min_order_qty": 1.0}
    except Exception as e:
        logging.error(f"Ошибка при чтении файла метаданных: {e}")
        return {}


def save_trade_log(
    trade_log_file: str | None,
    strategy_name: str, exchange: str, instrument: str, direction: str,
    entry_timestamp: datetime, exit_timestamp: datetime,
    entry_price: float, exit_price: float, pnl: float, exit_reason: str,
    interval: str, risk_manager: str
):
    """
    Записывает информацию о завершенной сделке в указанный файл в формате JSONL.
    Если trade_log_file равен None, функция ничего не делает.
    """

    if trade_log_file is None:
        return

    try:
        os.makedirs(os.path.dirname(trade_log_file), exist_ok=True)

        row_data = {
            'entry_timestamp_utc': entry_timestamp.isoformat(),
            'exit_timestamp_utc': exit_timestamp.isoformat(),
            'strategy_name': strategy_name,
            'exchange': exchange,
            'instrument': instrument,
            'direction': direction,
            'entry_price': round(entry_price, 4),
            'exit_price': round(exit_price, 4),
            'pnl': round(pnl, 4),
            'exit_reason': exit_reason,
            'interval': interval,
            'risk_manager': risk_manager
        }

        with open(trade_log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(row_data) + '\n')

    except (IOError, TypeError) as e:
        logging.error(f"Не удалось записать сделку в файл {trade_log_file}: {e}")
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при записи лога сделки: {e}")
