"""
Модуль расчета технических индикаторов (Feature Engineering).

Содержит класс `FeatureEngine`, который выступает оберткой над библиотекой `pandas-ta`.
Его задача — преобразовать декларативные требования стратегии (список необходимых индикаторов)
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

    Работает по принципу "Запрос-Ответ": Стратегия передает список требований,
    а движок добавляет соответствующие колонки в DataFrame.
    Поддерживает обновление данных в реальном времени (Live), корректно перезаписывая
    значения для новых свечей.
    """

    def __init__(self):
        """Инициализирует маппинг доступных индикаторов."""
        self._indicator_calculators = {
            "sma": self._calculate_sma,
            "ema": self._calculate_ema,
            "atr": self._calculate_atr,
            "bbands": self._calculate_bbands,
            "donchian": self._calculate_donchian,
            "adx": self._calculate_adx,
        }

    def add_required_features(self, data: pd.DataFrame, requirements: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Обогащает переданный DataFrame запрошенными индикаторами.

        Метод изменяет DataFrame **in-place** (по ссылке), добавляя новые колонки.
        Если расчет одного индикатора падает, остальные продолжают считаться.

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
                    logger.error(f"Ошибка расчета индикатора {indicator_name}: {e}")
            else:
                logger.warning(f"FeatureEngine: Неизвестный индикатор '{indicator_name}'. Пропускаем.")
        return data

    def _calculate_sma(self, data: pd.DataFrame, period: int, column: str = 'close'):
        """
        Считает простую скользящую среднюю (SMA).
        Колонка: `SMA_{period}` (или `SMA_{period}_{column}`).
        """
        col_name = f'SMA_{period}' if column == 'close' else f'SMA_{period}_{column}'
        data.ta.sma(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_ema(self, data: pd.DataFrame, period: int, column: str = 'close'):
        """
        Считает экспоненциальную скользящую среднюю (EMA).
        Колонка: `EMA_{period}`.
        """
        col_name = f'EMA_{period}' if column == 'close' else f'EMA_{period}_{column}'
        data.ta.ema(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_atr(self, data: pd.DataFrame, period: int):
        """
        Считает средний истинный диапазон (ATR).
        Колонка: `ATR_{period}`.
        """
        col_name = f'ATR_{period}'
        data.ta.atr(length=period, append=True, col_names=(col_name,))

    def _calculate_bbands(self, data: pd.DataFrame, period: int, std: float):
        """
        Считает Полосы Боллинджера (Bollinger Bands).
        Удаляет старые колонки перед расчетом во избежание конфликтов имен.

        Колонки:
            - `BBU_{period}_{std}` (Upper)
            - `BBL_{period}_{std}` (Lower)
            - `BBM_{period}_{std}` (Mid)
        """
        std_str = str(std).replace('.', '_')

        # Точечно удаляем только колонки для ЭТИХ параметров
        target_cols = [
            f'BBL_{period}_{std_str}',
            f'BBM_{period}_{std_str}',
            f'BBU_{period}_{std_str}',
            f'BBB_{period}_{std_str}',  # Bandwidth
            f'BBP_{period}_{std_str}'  # %B
        ]

        # Удаляем, если они уже есть
        cols_to_drop = [c for c in target_cols if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.bbands(length=period, std=std, append=True)

        # Чистим лишние служебные колонки, оставляя только линии
        extras = [f'BBB_{period}_{std_str}', f'BBP_{period}_{std_str}']
        data.drop(columns=[c for c in extras if c in data.columns], inplace=True, errors='ignore')

    def _calculate_donchian(self, data: pd.DataFrame, lower_period: int, upper_period: int):
        """
        Считает канал Дончиана (Donchian Channel).

        Колонки:
            - `DCU_{upper}_{lower}` (Upper)
            - `DCL_{upper}_{lower}` (Lower)
            - `DCM_{upper}_{lower}` (Mid)
        """
        target_cols = [
            f'DCL_{upper_period}_{lower_period}',
            f'DCU_{upper_period}_{lower_period}',
            f'DCM_{upper_period}_{lower_period}'
        ]

        cols_to_drop = [c for c in target_cols if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.donchian(lower_length=lower_period, upper_length=upper_period, append=True)

    def _calculate_adx(self, data: pd.DataFrame, period: int):
        """
        Считает индекс направленного движения (ADX).
        Удаляет промежуточные колонки DMP/DMN, оставляя только основную линию.

        Колонка: `ADX_{period}`.
        """
        final_col_name = f'ADX_{period}'

        raw_adx = f"ADX_{period}"
        raw_dmp = f"DMP_{period}"
        raw_dmn = f"DMN_{period}"

        target_cols = [raw_adx, raw_dmp, raw_dmn]

        cols_to_drop = [c for c in target_cols if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.adx(length=period, append=True)

        # Чистим побочные колонки
        for col in [raw_dmp, raw_dmn]:
            if col in data.columns:
                data.drop(columns=[col], inplace=True)