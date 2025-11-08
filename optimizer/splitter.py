import pandas as pd
from typing import List, Tuple, Generator
import numpy as np


def split_data_by_periods(data: pd.DataFrame, total_periods: int) -> List[pd.DataFrame]:
    """
    Делит DataFrame на указанное количество равных по времени частей.
    """
    if not isinstance(data, pd.DataFrame) or data.empty:
        return []

    # Используем np.array_split, который делит массив на N почти равных частей.
    # Это надежнее, чем делить по индексам, особенно если в данных есть пропуски.
    return np.array_split(data, total_periods)


def walk_forward_generator(
        data_periods: List[pd.DataFrame],
        train_periods: int,
        test_periods: int
) -> Generator[Tuple[pd.DataFrame, pd.DataFrame, int], None, None]:
    """
    Генератор, который создает пары (train_df, test_df) для Walk-Forward Optimization.

    :param data_periods: Список DataFrame'ов, где каждый элемент - один период.
    :param train_periods: Количество периодов для обучающей выборки.
    :param test_periods: Количество периодов для тестовой выборки.
    :yields: Кортеж (train_df, test_df, step_number).
    """
    total_periods = len(data_periods)

    # Рассчитываем количество "шагов" (сдвигов окна), которые мы можем сделать.
    num_steps = total_periods - train_periods - test_periods + 1

    if num_steps <= 0:
        raise ValueError("Недостаточно данных для WFO с заданными параметрами. "
                         "Уменьшите train_periods или test_periods.")

    for i in range(num_steps):
        # Определяем срезы для обучающей и тестовой выборок
        train_start = i
        train_end = i + train_periods
        test_start = train_end
        test_end = test_start + test_periods

        # "Склеиваем" части в единые DataFrame'ы
        train_df = pd.concat(data_periods[train_start:train_end], ignore_index=True)
        test_df = pd.concat(data_periods[test_start:test_end], ignore_index=True)

        yield train_df, test_df, i + 1