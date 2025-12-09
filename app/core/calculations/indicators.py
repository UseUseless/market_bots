"""
Модуль расчета технических индикаторов (Feature Engineering).

Содержит класс `FeatureEngine`, который выступает оберткой над библиотекой `pandas-ta`.
Его задача — преобразовать список необходимых индикаторов из стратегии
в конкретные числовые столбцы DataFrame.
"""

import pandas as pd
import logging
from typing import List, Dict, Any
import pandas_ta as ta

logger = logging.getLogger(__name__)


class FeatureEngine:
    """
    Сервис для расчета технических индикаторов.

    Стратегия передает список требований, а движок добавляет соответствующие колонки в DataFrame.
    """

    def __init__(self):
        """Инициализирует маппинг доступных индикаторов."""
        self._indicator_calculators = {
            "sma": self._calculate_sma,
            "ema": self._calculate_ema,
            "atr": self._calculate_atr,
            "rsi": self._calculate_rsi,
            "bbands": self._calculate_bbands,
            "donchian": self._calculate_donchian,
            "adx": self._calculate_adx,
        }

    def add_required_features(self, data: pd.DataFrame, requirements: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Добавляет в DataFrame запрошенные индикаторы.

        Метод изменяет DataFrame **in-place** (по ссылке).

        Args:
            data (pd.DataFrame): Исходные данные свечей (OHLCV).
            requirements (List[Dict]): Список конфигураций индикаторов.
                Пример: `[{"name": "sma", "params": {"period": 20}}]`.

        Returns:
            pd.DataFrame: Ссылка на тот же объект data, но с добавленными колонками.
        """
        for req in requirements:
            indicator_name = req.get("name")
            params = req.get("params", {})

            calculator = self._indicator_calculators.get(indicator_name)
            if calculator:
                try:
                    calculator(data, **params)
                except Exception as e:
                    logger.error(f"Ошибка расчета индикатора {indicator_name} с параметрами {params}: {e}")
            else:
                logger.warning(f"FeatureEngine: Неизвестный индикатор '{indicator_name}'. Пропускаем.")
        return data

    def _calculate_sma(self, data: pd.DataFrame, period: int, column: str = 'close'):
        """
        Считает простую скользящую среднюю (SMA).
        Колонка: `SMA_{period}`.
        """
        col_name = f'SMA_{period}'
        # Если колонка уже есть, pandas_ta может добавить дубль с суффиксом, поэтому удаляем заранее
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)
        data.ta.sma(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_ema(self, data: pd.DataFrame, period: int, column: str = 'close'):
        """
        Считает экспоненциальную скользящую среднюю (EMA).
        Колонка: `EMA_{period}`.
        """
        col_name = f'EMA_{period}'
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)

        data.ta.ema(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_atr(self, data: pd.DataFrame, period: int):
        """
        Считает средний истинный диапазон (ATR).
        Колонка: `ATR_{period}`.
        """
        col_name = f'ATR_{period}'
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)

        data.ta.atr(length=period, append=True, col_names=(col_name,))

    def _calculate_rsi(self, data: pd.DataFrame, period: int):
        """
        RSI (Relative Strength Index).
        Колонка: `RSI_{period}`.
        """
        col_name = f'RSI_{period}'
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)

        data.ta.rsi(length=period, append=True, col_names=(col_name,))

    def _calculate_bbands(self, data: pd.DataFrame, period: int, std: float):
        """
        Считает Полосы Боллинджера (Bollinger Bands).

        Колонки:
            - `BBL_{period}` (Lower)
            - `BBM_{period}` (Mid)
            - `BBU_{period}` (Upper)
            - `BBB_{period}` (Bandwidth)
            - `BBP_{period}` (%B)
        """
        std_str = str(std).replace('.', '_')

        # Точечно удаляем только колонки для ЭТИХ параметров
        col_names = (
            f'BBL_{period}',
            f'BBM_{period}',
            f'BBU_{period}',
            f'BBB_{period}',
            f'BBP_{period}'
        )

        # Удаляем старые, если есть (чтобы избежать конфликтов)
        cols_to_drop = [c for c in col_names if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.bbands(length=period, std=std, append=True, col_names=col_names)

    def _calculate_donchian(self, data: pd.DataFrame, lower_period: int, upper_period: int):
        """
        Считает канал Дончиана (Donchian Channel).

        Колонки:
            - `DCL_{upper}` (Lower)
            - `DCM_{upper}` (Mid)
            - `DCU_{upper}` (Upper)
        """
        col_names = (
            f'DCL_{upper_period}',
            f'DCM_{upper_period}',
            f'DCU_{upper_period}'
        )

        cols_to_drop = [c for c in col_names if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.donchian(lower_length=lower_period, upper_length=upper_period, append=True, col_names=col_names)

    def _calculate_adx(self, data: pd.DataFrame, period: int):
        """
        Считает индекс направленного движения (ADX).

        Колонки:
            - `ADX_{period}`
            - `DMP_{period}` (DI+)
            - `DMN_{period}` (DI-)
        """
        # Порядок pandas_ta для adx: ADX, DMP, DMN
        col_names = (
            f'ADX_{period}',
            f'DMP_{period}',
            f'DMN_{period}'
        )

        cols_to_drop = [c for c in col_names if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.adx(length=period, append=True, col_names=col_names)