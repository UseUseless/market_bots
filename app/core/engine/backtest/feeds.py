"""
Модуль поставщика данных для бэктеста.

Содержит реализацию `IDataFeed`, которая эмулирует потоковую передачу данных
на основе статического DataFrame. Это позволяет "проигрывать" историю свеча за свечой.
"""

import pandas as pd
from app.core.interfaces import IDataFeed


class BacktestDataFeed(IDataFeed):
    """
    Эмулятор потока рыночных данных.

    Работает по принципу курсора: хранит весь DataFrame в памяти, но открывает
    доступ к нему последовательно. Стратегия "видит" только те данные, которые
    находятся до текущего момента времени (индекса).

    Attributes:
        _data (pd.DataFrame): Полный набор исторических данных.
        _interval (str): Таймфрейм данных.
        _current_index (int): Указатель на "текущую" свечу в симуляции.
        _max_index (int): Последний доступный индекс в массиве.
    """

    def __init__(self, data: pd.DataFrame, interval: str):
        """
        Инициализирует фид.

        Args:
            data (pd.DataFrame): DataFrame с историей и предрасчитанными индикаторами.
                                 Должен быть отсортирован по времени.
            interval (str): Таймфрейм (например, '5min').
        """
        # Сбрасываем индекс, чтобы работать с integer-location (iloc) от 0 до N
        self._data = data.reset_index(drop=True)
        self._interval = interval
        self._current_index = -1
        self._max_index = len(self._data) - 1

    @property
    def interval(self) -> str:
        """Возвращает текущий таймфрейм."""
        return self._interval

    def next(self) -> bool:
        """
        Перемещает курсор времени на одну свечу вперед.

        Этот метод вызывает движок бэктеста в основном цикле.

        Returns:
            bool: True, если данные еще есть (симуляция продолжается).
                  False, если достигнут конец истории.
        """
        if self._current_index < self._max_index:
            self._current_index += 1
            return True
        return False

    def get_current_candle(self) -> pd.Series:
        """
        Возвращает данные "текущей" свечи (на которую указывает курсор).

        Returns:
            pd.Series: Строка DataFrame.

        Raises:
            ValueError: Если метод вызван до первого вызова `next()`.
        """
        if self._current_index < 0:
            raise ValueError("Feed не запущен. Сначала вызовите next().")
        return self._data.iloc[self._current_index]

    def get_history(self, length: int) -> pd.DataFrame:
        """
        Возвращает срез исторических данных относительно текущего момента.

        Диапазон: `[текущий_индекс - length + 1 : текущий_индекс + 1]`.
        То есть возвращает `length` последних свечей, включая текущую.

        Args:
            length (int): Глубина запрашиваемой истории.

        Returns:
            pd.DataFrame: Копия среза данных.
        """
        if self._current_index < 0:
            return pd.DataFrame()

        # Вычисляем начало окна. Не может быть меньше 0.
        start_index = max(0, self._current_index - length + 1)
        end_index = self._current_index + 1

        # Возвращаем копию, чтобы стратегия случайно не изменила исходные данные
        return self._data.iloc[start_index:end_index].copy()