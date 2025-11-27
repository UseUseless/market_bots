from abc import ABC, abstractmethod
from queue import Queue
from typing import Literal, Dict, Any, List

import pandas as pd

from app.core.portfolio.state import PortfolioState
from app.shared.events import OrderEvent, Event


class BaseExecutionHandler(ABC):
    """
    Абстрактный базовый класс для всех исполнителей ордеров.
    Определяет единый интерфейс для симулятора и реального исполнителя.
    """
    def __init__(self, events_queue: Queue):
        self.events_queue = events_queue

    @abstractmethod
    def execute_order(self, event: OrderEvent, last_candle: pd.Series = None):
        """
        Основной метод, который принимает OrderEvent и должен в конечном итоге
        сгенерировать FillEvent.

        :param event: Событие с деталями ордера.
        :param last_candle: Последняя доступная свеча. Обязательна для симулятора,
                            но может не использоваться в live-режиме.
        """
        raise NotImplementedError("Метод execute_order должен быть реализован.")


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


class IPublisher(ABC):
    """
    Интерфейс для публикации событий (сигналов).
    Позволяет стратегии не знать, куда уйдет сигнал (в консоль или Телеграм).
    """

    @abstractmethod
    async def publish(self, event: Event):
        """Отправляет событие в шину."""
        raise NotImplementedError


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


class IPortfolioRepository(ABC):
    """
    Интерфейс для сохранения и загрузки состояния портфеля.
    Позволяет ядру не зависеть от SQLAlchemy.
    """

    @abstractmethod
    async def save_portfolio_state(self, config_id: int, state: PortfolioState) -> None:
        """Сохраняет текущее состояние портфеля в БД."""
        raise NotImplementedError

    @abstractmethod
    async def load_portfolio_state(self, config_id: int, initial_capital: float) -> PortfolioState:
        """
        Загружает состояние из БД.
        Если состояния нет — создает новое (пустое) с initial_capital.
        """
        raise NotImplementedError
