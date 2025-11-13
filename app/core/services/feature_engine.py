import pandas as pd
import pandas_ta as ta
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

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
                logger.warning(f"FeatureEngine: Неизвестный индикатор '{indicator_name}'. Пропускаем.")
        return data


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
            original_cols = set(data.columns)
            data.ta.bbands(length=period, std=std, append=True)
            new_cols = set(data.columns) - original_cols

            rename_map = {}
            for col in new_cols:
                if col.startswith("BBL_"):
                    rename_map[col] = f'BBL_{period}_{std_str}'
                elif col.startswith("BBM_"):
                    rename_map[col] = f'BBM_{period}_{std_str}'
                elif col.startswith("BBU_"):
                    rename_map[col] = f'BBU_{period}_{std_str}'
                elif col.startswith("BBB_") or col.startswith("BBP_"):
                    rename_map[col] = None

            cols_to_drop = [k for k, v in rename_map.items() if v is None]
            data.drop(columns=cols_to_drop, inplace=True)

            final_rename_map = {k: v for k, v in rename_map.items() if v is not None}
            data.rename(columns=final_rename_map, inplace=True)

    def _calculate_donchian(self, data: pd.DataFrame, lower_period: int, upper_period: int):
        col_name_upper = f'DCU_{upper_period}_{lower_period}'
        if col_name_upper not in data.columns:
            data.ta.donchian(lower_length=lower_period, upper_length=upper_period, append=True)

    def _calculate_adx(self, data: pd.DataFrame, period: int):
        final_col_name = f'ADX_{period}'

        if final_col_name not in data.columns:
            original_cols = set(data.columns)
            data.ta.adx(length=period, append=True)
            new_cols = set(data.columns) - original_cols

            for col in new_cols:
                if col.startswith("ADX_"):
                    data.rename(columns={col: final_col_name}, inplace=True)
                else:
                    data.drop(columns=[col], inplace=True)