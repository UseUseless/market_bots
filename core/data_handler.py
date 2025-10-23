from abc import ABC
from queue import Queue
import pandas as pd
import logging
import os

from core.event import MarketEvent
from utils.trade_client import TinkoffTrader

class DataHandler(ABC):
    """
    Абстрактный базовый класс для всех поставщиков рыночных данных.
    """
    def __init__(self, events_queue: Queue, figi: str):
        self.events_queue = events_queue
        self.figi = figi
            
class HistoricLocalDataHandler(DataHandler):
    """
    "Глупый" поставщик данных, который читает их из локальных Parquet-файлов.
    """
    def __init__(self, events_queue: Queue, figi: str, interval_str: str, data_path: str = "data"):
        super().__init__(events_queue, figi)
        self.interval = interval_str
        self.data_path = data_path
        self.file_path = os.path.join(self.data_path, self.interval, f"{self.figi}.parquet")

    def load_raw_data(self) -> pd.DataFrame:
        """
        Загружает 'сырые' данные из локального Parquet файла.
        """
        logging.info(f"DataHandler (Local): Чтение данных из файла {self.file_path}...")
        try:
            df = pd.read_parquet(self.file_path)
            logging.info(f"DataHandler (Local): Успешно загружено {len(df)} свечей из файла.")
            return df
        except FileNotFoundError:
            logging.error(f"DataHandler (Local): Файл не найден: {self.file_path}")
            logging.error("Убедитесь, что вы скачали данные с помощью download_data.py")
            return pd.DataFrame()

    def start_streaming(self, data: pd.DataFrame):
        if data.empty:
            logging.warning("DataHandler (Local): Получен пустой DataFrame для стриминга.")
            return
            
        logging.info(f"DataHandler (Local): Начинаю потоковую передачу {len(data)} подготовленных свечей.")
        for i, row in data.iterrows():
            event = MarketEvent(
                timestamp=row['time'],
                figi=self.figi,
                data=row
            )
            self.events_queue.put(event)