"""
Модуль интерфейсов и абстракций (Ports).

Определяет контракты, которые должны реализовать компоненты инфраструктуры
(адаптеры бирж, базы данных, исполнители ордеров).
Это позволяет ядру системы (Core) не зависеть от конкретных библиотек или API.
"""

from abc import ABC, abstractmethod
from queue import Queue
from typing import Dict, Any, List

import pandas as pd

from app.core.portfolio.state import PortfolioState
from app.shared.events import OrderEvent, Event


class BaseExecutionHandler(ABC):
    """
    Абстракция исполнителя ордеров.

    Отвечает за превращение намерения (OrderEvent) в факт сделки (FillEvent).
    В режиме бэктеста это симуляция, в режиме лайв — отправка запроса на биржу.
    """

    def __init__(self, events_queue: Queue):
        """
        Args:
            events_queue (Queue): Очередь, куда будут помещаться события
                исполнения (FillEvent) после завершения сделки.
        """
        self.events_queue = events_queue

    @abstractmethod
    def execute_order(self, event: OrderEvent, last_candle: pd.Series = None):
        """
        Выполняет ордер.

        Args:
            event (OrderEvent): Объект ордера с направлением, объемом и тикером.
            last_candle (pd.Series, optional): Последние рыночные данные.
                Обязательны для симулятора (для расчета проскальзывания).
                В Live-режиме могут игнорироваться.

        Returns:
            None: Результат работы (FillEvent) отправляется в очередь асинхронно.
        """
        raise NotImplementedError("Метод execute_order должен быть реализован.")


class IDataFeed(ABC):
    """
    Интерфейс поставщика рыночных данных для стратегии.

    Обеспечивает унифицированный доступ к историческим данным и текущей свече,
    скрывая источник данных (CSV-файл, память или WebSocket).
    """

    @abstractmethod
    def get_history(self, length: int) -> pd.DataFrame:
        """
        Возвращает срез исторических данных.

        Args:
            length (int): Количество последних свечей (включая текущую).

        Returns:
            pd.DataFrame: DataFrame с колонками OHLCV и индикаторами.
        """
        raise NotImplementedError

    @abstractmethod
    def get_current_candle(self) -> pd.Series:
        """
        Возвращает последнюю доступную (полностью закрытую) свечу.

        Returns:
            pd.Series: Строка данных с ценами и индикаторами.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def interval(self) -> str:
        """
        Возвращает таймфрейм потока данных (например, '5min').

        Returns:
            str: Строковое представление интервала.
        """
        raise NotImplementedError


class IPublisher(ABC):
    """
    Интерфейс для публикации событий (Observer Pattern).

    Позволяет компонентам отправлять сообщения (например, сигналы),
    не зная, кто их получит (Telegram, логгер или БД).
    """

    @abstractmethod
    async def publish(self, event: Event):
        """
        Асинхронно отправляет событие подписчикам.

        Args:
            event (Event): Объект события (SignalEvent, MarketEvent и т.д.).
        """
        raise NotImplementedError


class BaseDataClient(ABC):
    """
    Контракт для клиентов, получающих данные от API биржи (Market Data).
    """

    @abstractmethod
    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """
        Загружает исторические свечи (K-Lines).

        Args:
            instrument (str): Тикер инструмента.
            interval (str): Интервал свечей.
            days (int): Глубина истории в днях.
            **kwargs: Дополнительные параметры (например, category='linear' для Bybit).

        Returns:
            pd.DataFrame: DataFrame с колонками ['time', 'open', 'high', 'low', 'close', 'volume'].
        """
        raise NotImplementedError

    @abstractmethod
    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        """
        Запрашивает спецификацию инструмента.

        Args:
            instrument (str): Тикер инструмента.

        Returns:
            Dict[str, Any]: Словарь с параметрами (min_order_qty, qty_step, lot_size).
        """
        raise NotImplementedError

    @abstractmethod
    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        """
        Возвращает список самых ликвидных инструментов по обороту.

        Args:
            count (int): Количество инструментов в топе.

        Returns:
            List[str]: Список тикеров.
        """
        raise NotImplementedError


class IPortfolioRepository(ABC):
    """
    Интерфейс для персистентности (сохранения) состояния портфеля.
    Позволяет ядру не зависеть от конкретной БД или ORM.
    """

    @abstractmethod
    async def save_portfolio_state(self, config_id: int, state: PortfolioState) -> None:
        """
        Сохраняет текущий капитал и открытые позиции в базу данных.

        Args:
            config_id (int): ID конфигурации стратегии.
            state (PortfolioState): Объект состояния.
        """
        raise NotImplementedError

    @abstractmethod
    async def load_portfolio_state(self, config_id: int, initial_capital: float) -> PortfolioState:
        """
        Загружает последнее сохраненное состояние портфеля.

        Args:
            config_id (int): ID конфигурации стратегии.
            initial_capital (float): Капитал по умолчанию, если записи в БД нет.

        Returns:
            PortfolioState: Восстановленный объект состояния.
        """
        raise NotImplementedError