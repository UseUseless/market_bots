"""
Модуль расчета технических индикаторов (Feature Engineering).

Содержит класс `FeatureEngine`, который выступает оберткой над библиотекой `pandas-ta`.
Его задача — преобразовать декларативный список требований индикаторов из конфигурации
стратегии в конкретные числовые столбцы DataFrame.
"""

import logging
from typing import List, Dict, Any, Optional

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)


class FeatureEngine:
    """
    Сервис для расчета технических индикаторов.

    Использует библиотеку pandas-ta для векторных вычислений.
    Позволяет стратегиям запрашивать индикаторы через конфигурационные словари,
    не заботясь о деталях реализации вызовов библиотеки.
    """

    def __init__(self):
        """
        Инициализирует движок и регистрирует доступные калькуляторы.
        """
        # Маппинг строковых имен индикаторов на методы класса
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

        Метод изменяет DataFrame **in-place** (по ссылке), добавляя новые колонки.

        Args:
            data (pd.DataFrame): Исходные данные свечей (OHLCV).
                Должен содержать колонки: open, high, low, close, volume.
            requirements (List[Dict]): Список конфигураций индикаторов.
                Пример: `[{"name": "sma", "params": {"period": 20}}]`.

        Returns:
            pd.DataFrame: Ссылка на тот же объект data, но с добавленными колонками.
        """
        if data.empty:
            return data

        for req in requirements:
            indicator_name = req.get("name", "").lower()
            params = req.get("params", {})

            calculator = self._indicator_calculators.get(indicator_name)

            if calculator:
                try:
                    # Вызов соответствующего метода расчета с распаковкой параметров
                    calculator(data, **params)
                except Exception as e:
                    logger.error(
                        f"FeatureEngine: Ошибка расчета {indicator_name} с параметрами {params}: {e}",
                        exc_info=True
                    )
            else:
                logger.warning(f"FeatureEngine: Неизвестный индикатор '{indicator_name}'. Пропускаем.")

        return data

    # --- Implementations ---

    def _calculate_sma(self, data: pd.DataFrame, period: int, column: str = 'close'):
        """
        Считает простую скользящую среднюю (SMA).

        Args:
            data: DataFrame с данными.
            period: Период усреднения.
            column: Колонка, по которой считать (default: close).

        Output Column: `SMA_{period}`
        """
        col_name = f'SMA_{period}'

        # Удаляем колонку, если она уже есть, чтобы pandas-ta не создавал дубли (SMA_20_1)
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)

        data.ta.sma(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_ema(self, data: pd.DataFrame, period: int, column: str = 'close'):
        """
        Считает экспоненциальную скользящую среднюю (EMA).

        Args:
            data: DataFrame с данными.
            period: Период усреднения.
            column: Колонка источника.

        Output Column: `EMA_{period}`
        """
        col_name = f'EMA_{period}'
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)

        data.ta.ema(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_atr(self, data: pd.DataFrame, period: int):
        """
        Считает средний истинный диапазон (ATR).

        Args:
            data: DataFrame.
            period: Период сглаживания.

        Output Column: `ATR_{period}`
        """
        col_name = f'ATR_{period}'
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)

        data.ta.atr(length=period, append=True, col_names=(col_name,))

    def _calculate_rsi(self, data: pd.DataFrame, period: int):
        """
        RSI (Relative Strength Index).

        Args:
            data: DataFrame.
            period: Период.

        Output Column: `RSI_{period}`
        """
        col_name = f'RSI_{period}'
        if col_name in data.columns:
            data.drop(columns=[col_name], inplace=True)

        data.ta.rsi(length=period, append=True, col_names=(col_name,))

    def _calculate_bbands(self, data: pd.DataFrame, period: int, std: float):
        """
        Считает Полосы Боллинджера (Bollinger Bands).

        Args:
            data: DataFrame.
            period: Период средней.
            std: Множитель стандартного отклонения.

        Output Columns:
            - `BBL_{period}` (Lower)
            - `BBM_{period}` (Mid)
            - `BBU_{period}` (Upper)
            - `BBB_{period}` (Bandwidth)
            - `BBP_{period}` (%B)
        """
        # Формируем имена колонок жестко, чтобы избежать авто-именования pandas-ta
        # std может быть дробным (2.5), pandas-ta форматирует это специфично,
        # поэтому задаем имена вручную для предсказуемости в стратегиях.
        col_names = (
            f'BBL_{period}',
            f'BBM_{period}',
            f'BBU_{period}',
            f'BBB_{period}',
            f'BBP_{period}'
        )

        # Очистка старых данных
        cols_to_drop = [c for c in col_names if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.bbands(length=period, std=std, append=True, col_names=col_names)

    def _calculate_donchian(self, data: pd.DataFrame, lower_period: int, upper_period: int):
        """
        Считает канал Дончиана (Donchian Channel).

        Args:
            data: DataFrame.
            lower_period: Период для нижней границы.
            upper_period: Период для верхней границы.

        Output Columns:
            - `DCL_{upper}` (Lower)
            - `DCM_{upper}` (Mid)
            - `DCU_{upper}` (Upper)
        """
        # Используем upper_period как суффикс для унификации
        col_names = (
            f'DCL_{upper_period}',
            f'DCM_{upper_period}',
            f'DCU_{upper_period}'
        )

        cols_to_drop = [c for c in col_names if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.donchian(
            lower_length=lower_period,
            upper_length=upper_period,
            append=True,
            col_names=col_names
        )

    def _calculate_adx(self, data: pd.DataFrame, period: int):
        """
        Считает индекс направленного движения (ADX).

        Args:
            data: DataFrame.
            period: Период сглаживания.

        Output Columns:
            - `ADX_{period}`
            - `DMP_{period}` (Positive Directional Index)
            - `DMN_{period}` (Negative Directional Index)
        """
        col_names = (
            f'ADX_{period}',
            f'DMP_{period}',
            f'DMN_{period}'
        )

        cols_to_drop = [c for c in col_names if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.adx(length=period, append=True, col_names=col_names)