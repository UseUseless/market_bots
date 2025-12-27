"""
Интерфейсы и абстракции (Ports).

Определяет контракты, которые нужно реализовать для унифицированной работы.
Это позволяет ядру системы (Core) не зависеть от конкретных библиотек или API.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List
import pandas as pd

from app.shared.events import SignalEvent


class ExchangeDataGetter(ABC):
    """
    Получение данных от API биржи (Market Data).
    (для поддержки разных бирж, добавления новых)
    """

    @abstractmethod
    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """
        Загружает исторические свечи.

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


class MarketDataProvider(ABC):
    """
    Подача данных в стратегию

    Доступ к историческим данным и текущей свече,
    скрывая источник данных (parquet-файл, память или WebSocket).
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


class SignalHandler(ABC):
    """Интерфейс для обработки сигналов (Telegram, DB, Discord, Email, etc.)"""

    @abstractmethod
    async def handle_signal(self, event: SignalEvent) -> None:
        """
        Метод должен вызываться после того, как стратегия сгенерировала сигнал.
        Реализация метода не должна блокировать событийный цикл (Event Loop) на длительное время.

        Args:
            event (SignalEvent): Объект события сигнала, содержащий данные
                об инструменте, направлении, цене и времени возникновения.

        Returns:
            None
        """
        pass