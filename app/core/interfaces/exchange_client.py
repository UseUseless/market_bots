from abc import ABC, abstractmethod
from typing import Literal, List, Dict, Any
import pandas as pd

TradeModeType = Literal["REAL", "SANDBOX"]

class BaseDataClient(ABC):
    """Абстрактный 'контракт' для всех клиентов, поставляющих рыночные данные ИЗВНЕ (через API)."""

    @abstractmethod
    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """Загружает исторические свечи."""
        raise NotImplementedError

    @abstractmethod
    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        """Загружает метаданные об инструменте (лот, шаг цены и т.д.)."""
        raise NotImplementedError

    @abstractmethod
    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        """Возвращает список самых ликвидных инструментов."""
        raise NotImplementedError

class BaseTradeClient(ABC):
    """Абстрактный 'контракт' для всех клиентов, исполняющих ордера (через API)."""

    @abstractmethod
    def place_market_order(self, instrument_id: str, quantity: float, direction: str, **kwargs):
        """Размещает рыночный ордер."""
        raise NotImplementedError