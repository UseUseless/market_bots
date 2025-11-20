from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import pandas as pd


class IDataFeed(ABC):
    """
    Интерфейс поставщика данных.
    Гарантирует, что стратегия получит данные одинаково
    и в бэктесте (из файла), и в лайве (из вебсокета).
    """

    @abstractmethod
    def get_history(self, length: int) -> pd.DataFrame:
        """
        Возвращает исторический буфер данных.
        :param length: Сколько последних свечей вернуть.
        """
        raise NotImplementedError

    @abstractmethod
    def get_current_candle(self) -> pd.Series:
        """Возвращает последнюю полностью сформированную свечу."""
        raise NotImplementedError

    @property
    @abstractmethod
    def interval(self) -> str:
        """Таймфрейм данных (например, '1min')."""
        raise NotImplementedError