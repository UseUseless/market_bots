from abc import ABC
from queue import Queue
import pandas as pd
import logging
import os

class DataHandler(ABC):
    """
    Абстрактный базовый класс для всех поставщиков рыночных данных.
    Будет использоваться для создания других хэндлеров (например в Live режиме для Tinkoff, Binance)
    Может будет дополняться
    """
    def __init__(self, events_queue: Queue, figi: str):
        self.events_queue = events_queue
        self.figi = figi
            
class HistoricLocalDataHandler(DataHandler):
    """
    Читает локальные Parquet-файлы и создаёт pandas df
    """
    def __init__(self, events_queue: Queue, figi: str, interval_str: str, data_path: str = "data"):
        super().__init__(events_queue, figi)
        self.interval = interval_str
        self.data_path = data_path
        self.file_path = os.path.join(self.data_path, self.interval, f"{self.figi}.parquet")

    def load_raw_data(self) -> pd.DataFrame:
        """
        Загружает данные из локального Parquet файла.
        """
        logging.info(f"DataHandler (Local): Чтение данных из файла {self.file_path}...")
        # Читает локальные исторические файлы и передаёт их как pd.DataFrame
        try:
            df = pd.read_parquet(self.file_path)
            logging.info(f"DataHandler (Local): Успешно загружено {len(df)} свечей из файла.")
            return df
        # Если файла не нашлось, возвращает пустой pd.DataFrame
        except FileNotFoundError:
            logging.error(f"DataHandler (Local): Файл не найден: {self.file_path}")
            logging.error("Убедитесь, что вы скачали данные с помощью download_data.py")
            return pd.DataFrame()