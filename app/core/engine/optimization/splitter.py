"""
Модуль разделения данных (Data Splitter).

Содержит утилиты для подготовки данных к процедуре Walk-Forward Optimization (WFO).
В отличие от классического Cross-Validation, здесь сохраняется хронологический порядок,
чтобы избежать "заглядывания в будущее".

Основные функции:
1.  **Разбиение на периоды**: Делит всю историю на N равных частей.
2.  **Генерация окон**: Формирует скользящие окна (Train + Test) для шагов оптимизации.
"""

import pandas as pd
import numpy as np
from typing import List, Tuple, Generator


def split_data_by_periods(data: pd.DataFrame, total_periods: int) -> List[pd.DataFrame]:
    """
    Разбивает DataFrame на указанное количество частей.

    Использует разбиение по количеству строк (`np.array_split`), а не по календарным датам.
    Это обеспечивает равномерный объем данных в каждом периоде, независимо от
    торговой активности или пропусков торгов.

    Args:
        data (pd.DataFrame): Исходный набор данных.
        total_periods (int): На сколько частей нужно разбить данные.

    Returns:
        List[pd.DataFrame]: Список DataFrame'ов (чанков).
    """
    if not isinstance(data, pd.DataFrame) or data.empty:
        return []

    # np.array_split корректно работает с DataFrame, возвращая список DataFrame'ов.
    # Если total_periods не делит длину нацело, последние чанки будут чуть меньше.
    return np.array_split(data, total_periods)


def walk_forward_generator(
        data_periods: List[pd.DataFrame],
        train_periods: int,
        test_periods: int
) -> Generator[Tuple[pd.DataFrame, pd.DataFrame, int], None, None]:
    """
    Генератор скользящих окон для WFO.

    Создает последовательность пар (Train, Test) для каждого шага оптимизации.
    Окно сдвигается на 1 период вперед на каждом шаге.

    Пример:
        Periods: [A, B, C, D, E], Train=2, Test=1
        Step 1: Train=[A, B], Test=[C]
        Step 2: Train=[B, C], Test=[D]
        Step 3: Train=[C, D], Test=[E]

    Args:
        data_periods (List[pd.DataFrame]): Список предварительно нарезанных периодов.
        train_periods (int): Количество периодов в обучающей выборке (In-Sample).
        test_periods (int): Количество периодов в тестовой выборке (Out-of-Sample).

    Yields:
        Tuple[pd.DataFrame, pd.DataFrame, int]:
            1. Train DataFrame (объединенный).
            2. Test DataFrame (объединенный).
            3. Номер текущего шага (начиная с 1).

    Raises:
        ValueError: Если данных недостаточно для формирования хотя бы одного окна.
    """
    total_periods = len(data_periods)

    # Рассчитываем количество возможных шагов
    num_steps = total_periods - train_periods - test_periods + 1

    if num_steps <= 0:
        raise ValueError(
            f"Недостаточно данных для WFO. Всего периодов: {total_periods}, "
            f"нужно минимум: {train_periods + test_periods}."
        )

    for i in range(num_steps):
        # Индексы для текущего окна
        train_start = i
        train_end = i + train_periods
        test_start = train_end
        test_end = test_start + test_periods

        # Склеиваем периоды в единые DataFrame для обучения и теста
        # ignore_index=True важен, чтобы перестроить индекс от 0 до N
        train_df = pd.concat(data_periods[train_start:train_end], ignore_index=True)
        test_df = pd.concat(data_periods[test_start:test_end], ignore_index=True)

        yield train_df, test_df, i + 1