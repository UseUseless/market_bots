import pandas as pd
import pandas_ta as ta
import logging
from typing import List, Dict, Any


class FeatureEngine:
    """
    Отвечает за расчет технических индикаторов по требованию.
    Стратегия декларирует, какие индикаторы ей нужны, а FeatureEngine
    выполняет только запрошенные вычисления.
    """

    def __init__(self):
        """Инициализирует диспетчер с известными ему индикаторами."""
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
        Главный метод. Принимает DataFrame и список требований,
        добавляет в DataFrame только запрошенные индикаторы.
        """
        for req in requirements:
            indicator_name = req.get("name")
            params = req.get("params", {})

            calculator = self._indicator_calculators.get(indicator_name)
            if calculator:
                calculator(data, **params)
            else:
                logging.warning(f"FeatureEngine: Неизвестный индикатор '{indicator_name}'. Пропускаем.")
        return data

    # --- Приватные методы для расчета конкретных индикаторов ---

    def _calculate_sma(self, data: pd.DataFrame, period: int, column: str = 'close'):
        col_name = f'SMA_{period}' if column == 'close' else f'SMA_{period}_{column}'
        if col_name not in data.columns:
            data.ta.sma(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_ema(self, data: pd.DataFrame, period: int, column: str = 'close'):
        col_name = f'EMA_{period}' if column == 'close' else f'EMA_{period}_{column}'
        if col_name not in data.columns:
            data.ta.ema(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_atr(self, data: pd.DataFrame, period: int):
        col_name = f'ATR_{period}'
        if col_name not in data.columns:
            data.ta.atr(length=period, append=True, col_names=(col_name,))

    def _calculate_bbands(self, data: pd.DataFrame, period: int, std: float):
        std_str = str(std).replace('.', '_')
        final_col_name_mid = f'BBM_{period}_{std_str}'

        if final_col_name_mid not in data.columns:
            # 1. Рассчитываем индикатор в отдельный DataFrame
            bbands_df = data.ta.bbands(length=period, std=std, append=False)

            # 2. Определяем наши финальные, стандартные имена колонок
            final_lower_name = f'BBL_{period}_{std_str}'
            final_mid_name = f'BBM_{period}_{std_str}'
            final_upper_name = f'BBU_{period}_{std_str}'

            # 3. Явно присваиваем колонки по их порядковому номеру из результата
            # Это самый надежный способ, не зависящий от имен в bbands_df
            data[final_lower_name] = bbands_df.iloc[:, 0]  # Первая колонка всегда BBL
            data[final_mid_name] = bbands_df.iloc[:, 1]  # Вторая колонка всегда BBM
            data[final_upper_name] = bbands_df.iloc[:, 2]  # Третья колонка всегда BBU

    def _calculate_donchian(self, data: pd.DataFrame, lower_period: int, upper_period: int):
        col_name_upper = f'DCU_{upper_period}_{lower_period}'
        if col_name_upper not in data.columns:
            data.ta.donchian(lower_length=lower_period, upper_length=upper_period, append=True)

    def _calculate_adx(self, data: pd.DataFrame, period: int):
        final_col_name = f'ADX_{period}'

        if final_col_name not in data.columns:
            # 1. Рассчитываем индикатор в отдельный DataFrame
            adx_df = data.ta.adx(length=period, append=False)

            # 2. Явно присваиваем только ту колонку, которая нам нужна (ADX)
            # Она всегда первая в результате.
            data[final_col_name] = adx_df.iloc[:, 0]