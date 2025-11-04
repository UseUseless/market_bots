from abc import ABC
from queue import Queue
import pandas as pd
import logging
import os
from datetime import time

from core.event import Event

class DataHandler(ABC):
    """
    Абстрактный базовый класс для всех поставщиков рыночных данных.
    Будет использоваться для создания других хэндлеров (например в Live режиме для Tinkoff, Binance)
    Может будет дополняться
    """
    def __init__(self, events_queue: Queue['Event'], instrument_id: str):
        self.events_queue = events_queue
        self.instrument_id = instrument_id


class HistoricLocalDataHandler(DataHandler):
    """
    Читает локальные Parquet-файлы из структурированной папки (data/exchange/interval),
    фильтрует их для основной торговой сессии (если нужно) и создаёт pandas df.
    """

    def __init__(self, events_queue: Queue['Event'], exchange: str, instrument_id: str, interval_str: str,
                 data_path: str):
        super().__init__(events_queue, instrument_id)
        self.exchange = exchange
        self.interval = interval_str
        self.data_path = data_path
        self.file_path = os.path.join(self.data_path, self.exchange, self.interval,
                                      f"{self.instrument_id.upper()}.parquet")

    def _filter_main_session(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Фильтрует DataFrame, оставляя только данные основной торговой сессии MOEX.
        (09:50-18:30 MSK, что соответствует 06:50-15:30 UTC).
        """
        main_session_start = time(6, 50)
        main_session_end = time(15, 30)

        original_rows = len(df)
        df_filtered = df[
            (df['time'].dt.time >= main_session_start) &
            (df['time'].dt.time <= main_session_end) &
            (df['time'].dt.dayofweek.isin([0, 1, 2, 3, 4]))
            ].copy()

        filtered_rows = len(df_filtered)
        if original_rows > 0:
            logging.info(f"Фильтрация по основной сессии MOEX: {original_rows} -> {filtered_rows} свечей.")
        return df_filtered

    def load_raw_data(self) -> pd.DataFrame:
        """
        Загружает данные из локального Parquet файла и применяет фильтрацию.
        """
        logging.info(f"DataHandler (Local): Чтение данных из файла {self.file_path}...")
        try:
            df = pd.read_parquet(self.file_path)
            logging.info(f"DataHandler (Local): Успешно загружено {len(df)} свечей из файла.")

            if self.exchange == 'tinkoff':
                df = self._filter_main_session(df)

            return df
        except FileNotFoundError:
            logging.error(f"DataHandler (Local): Файл не найден: {self.file_path}")
            logging.error("Убедитесь, что вы скачали данные с помощью download_data.py")
            return pd.DataFrame()