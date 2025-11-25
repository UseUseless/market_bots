from abc import ABC, abstractmethod
import pandas as pd

class IDataFeed(ABC):
    """
    Интерфейс Поставщика Данных.
    Гарантирует, что Стратегия и ML-модель получают данные одинаково
    и в Бэктесте (файлы), и в Лайве (WebSocket).
    """

    @abstractmethod
    def get_history(self, length: int) -> pd.DataFrame:
        """
        Возвращает N последних свечей (включая текущую только что закрытую).
        Критически важно для расчета индикаторов (SMA, RSI) и ML-фичей.
        """
        raise NotImplementedError

    @abstractmethod
    def get_current_candle(self) -> pd.Series:
        """Возвращает последнюю полностью закрытую свечу."""
        raise NotImplementedError

    @property
    @abstractmethod
    def interval(self) -> str:
        """Таймфрейм потока данных."""
        raise NotImplementedError