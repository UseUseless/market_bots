"""
Операции ввода-вывода файлов.

Этот модуль отвечает за чтение и запись данных, которые хранятся в файловой системе,
а не в базе данных. В основном это касается:
1. Логов сделок (формат JSONL) для анализа результатов.
2. Метаданных инструментов (формат JSON) для настройки лотности и шага цены.
"""

import json
import os
import logging
from typing import Dict, Any, Optional

import pandas as pd

from app.shared.config import config
from app.shared.types import Trade
from app.shared.schemas import TradingConfig

PATH_CONFIG = config.PATH_CONFIG
logger = logging.getLogger(__name__)


def load_trades_from_file(file_path: str) -> pd.DataFrame:
    """
    Загружает историю сделок из файла JSONL в DataFrame.

    Используется в Dashboards и аналитических скриптах для построения отчетов.

    Args:
        file_path (str): Полный путь к файлу логов.

    Returns:
        pd.DataFrame: Таблица со сделками.

    Raises:
        FileNotFoundError: Если файл не существует.
        ValueError: Если расширение файла не .jsonl.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл с логами сделок не найден: {file_path}")

    if not file_path.endswith('.jsonl'):
        raise ValueError("Неподдерживаемый формат файла логов. Используйте .jsonl")

    # lines=True позволяет читать файл, где каждая строка — отдельный JSON объект
    return pd.read_json(file_path, lines=True)


def load_instrument_info(
        exchange: str,
        instrument: str,
        interval: str,
        data_dir: str = PATH_CONFIG["DATA_DIR"]
) -> Dict[str, Any]:
    """
    Загружает метаданные инструмента (шаг цены, размер лота) из JSON-файла.

    Если файл отсутствует, возвращает безопасные значения по умолчанию (1.0),
    чтобы бэктест мог запуститься даже на неполных данных.

    Args:
        exchange (str): Биржа.
        instrument (str): Тикер.
        interval (str): Интервал (используется для формирования пути).
        data_dir (str): Корневая папка данных.

    Returns:
        Dict[str, Any]: Словарь с ключами 'lot_size', 'qty_step', 'min_order_qty'.
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