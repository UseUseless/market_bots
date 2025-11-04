import json
import os
import logging
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


def load_instrument_info(instrument: str, interval: str, data_dir: str = PATH_CONFIG["DATA_DIR"]) -> Dict[str, Any]:
    """
    Загружает метаданные об инструменте из .json файла.
    Возвращает словарь с правилами или значения по умолчанию.
    """
    file_path = os.path.join(data_dir, interval, f"{instrument.upper()}.json")
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