import pandas as pd
from app.core.interfaces import IDataFeed


class BacktestDataFeed(IDataFeed):
    """
    Эмулятор потока данных для бэктеста.
    Позволяет стратегии запрашивать историю get_history(),
    как будто она работает в Live-режиме.
    """

    def __init__(self, data: pd.DataFrame, interval: str):
        """
        :param data: Полный DataFrame с историческими данными (уже предобработанный).
        :param interval: Таймфрейм (например, '5min').
        """
        self._data = data.reset_index(drop=True)
        self._interval = interval
        self._current_index = -1
        self._max_index = len(self._data) - 1

    @property
    def interval(self) -> str:
        return self._interval

    def next(self) -> bool:
        """
        Перемещает курсор на следующую свечу.
        Возвращает False, если данные закончились.
        """
        if self._current_index < self._max_index:
            self._current_index += 1
            return True
        return False

    def get_current_candle(self) -> pd.Series:
        """Возвращает текущую свечу (на которую указывает курсор)."""
        if self._current_index < 0:
            raise ValueError("Feed not started. Call next() first.")
        return self._data.iloc[self._current_index]

    def get_history(self, length: int) -> pd.DataFrame:
        """
        Возвращает срез данных [current - length + 1 : current + 1].
        То есть последние N свечей, заканчивая текущей.
        """
        if self._current_index < 0:
            return pd.DataFrame()

        start_index = max(0, self._current_index - length + 1)
        end_index = self._current_index + 1

        return self._data.iloc[start_index:end_index].copy()