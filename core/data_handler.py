from abc import ABC, abstractmethod
from queue import Queue
import pandas as pd
import logging

from core.event import MarketEvent
from utils.trade_client import TinkoffTrader

class DataHandler(ABC):
    """
    Абстрактный базовый класс для всех поставщиков рыночных данных.
    """
    def __init__(self, events_queue: Queue, figi: str):
        self.events_queue = events_queue
        self.figi = figi

class HistoricTinkoffDataHandler(DataHandler):
    """
    "Глупый" поставщик данных. Его единственные задачи:
    1. Загрузить "сырые" исторические данные из Tinkoff API по запросу.
    2. Начать потоковую передачу (стриминг) УЖЕ ПОДГОТОВЛЕННЫХ данных,
       которые ему передадут извне.
    """
    def __init__(self, events_queue: Queue, figi: str, days_to_backtest: int, candle_interval: str):
        super().__init__(events_queue, figi)
        self.days = days_to_backtest
        self.interval = candle_interval
        self.trader = TinkoffTrader("sandbox")

    def load_raw_data(self) -> pd.DataFrame:
        """
        Загружает 'сырые' данные (OHLCV) без каких-либо индикаторов.
        """
        logging.info("DataHandler: Загрузка сырых исторических данных...")
        df = self.trader.get_historical_data(self.figi, self.days, self.interval)
        
        if df.empty:
            logging.error("DataHandler: Не удалось загрузить исторические данные.")
        else:
            logging.info(f"DataHandler: Успешно загружено {len(df)} свечей.")
            
        return df

    def start_streaming(self, data: pd.DataFrame):
        """
        Начинает 'стримить' (помещать в очередь событий) уже подготовленные
        и обогащенные индикаторами данные, свеча за свечой.
        """
        if data.empty:
            logging.warning("DataHandler: Получен пустой DataFrame для стриминга. Потоковая передача не начнется.")
            return
            
        logging.info(f"DataHandler: Начинаю потоковую передачу {len(data)} подготовленных свечей.")
        for i, row in data.iterrows():
            event = MarketEvent(
                timestamp=row['time'],
                figi=self.figi,
                data=row
            )
            self.events_queue.put(event)
            
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