import pandas as pd
import logging
from typing import List, Dict, Any
import pandas_ta as ta

logger = logging.getLogger(__name__)


class FeatureEngine:
    """
    Отвечает за расчет технических индикаторов по требованию.
    """

    def __init__(self):
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
        рассчитывает и добавляет индикаторы.
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
        # SMA просто перезаписывает колонку, если она есть, проблем нет.
        col_name = f'SMA_{period}' if column == 'close' else f'SMA_{period}_{column}'
        data.ta.sma(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_ema(self, data: pd.DataFrame, period: int, column: str = 'close'):
        col_name = f'EMA_{period}' if column == 'close' else f'EMA_{period}_{column}'
        data.ta.ema(length=period, close=column, append=True, col_names=(col_name,))

    def _calculate_atr(self, data: pd.DataFrame, period: int):
        col_name = f'ATR_{period}'
        data.ta.atr(length=period, append=True, col_names=(col_name,))

    def _calculate_bbands(self, data: pd.DataFrame, period: int, std: float):
        std_str = str(std).replace('.', '_')

        # Точечно удаляем только колонки для ЭТИХ параметров
        # Имена по умолчанию в pandas-ta: BBL_length_std, BBM_..., BBU_...
        target_cols = [
            f'BBL_{period}_{std_str}',
            f'BBM_{period}_{std_str}',
            f'BBU_{period}_{std_str}',
            f'BBB_{period}_{std_str}',  # Bandwidth
            f'BBP_{period}_{std_str}'  # %B
        ]

        # Удаляем, если они уже есть (чтобы pandas-ta не создала дубликаты или не сломалась)
        cols_to_drop = [c for c in target_cols if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.bbands(length=period, std=std, append=True)

        # Чистим лишние колонки (Bandwidth и %B), если они нам не нужны (по логике старого кода мы их удаляли)
        # Оставляем только L, M, U
        extras = [f'BBB_{period}_{std_str}', f'BBP_{period}_{std_str}']
        data.drop(columns=[c for c in extras if c in data.columns], inplace=True, errors='ignore')

    def _calculate_donchian(self, data: pd.DataFrame, lower_period: int, upper_period: int):
        # Ожидаемые имена от pandas-ta: DCL_upper_lower, DCU_upper_lower, DCM...
        # Важно: pandas-ta использует порядок {upper}_{lower} в именах колонок

        target_cols = [
            f'DCL_{upper_period}_{lower_period}',
            f'DCU_{upper_period}_{lower_period}',
            f'DCM_{upper_period}_{lower_period}'
        ]

        # Удаляем строго только эти колонки перед пересчетом
        cols_to_drop = [c for c in target_cols if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.donchian(lower_length=lower_period, upper_length=upper_period, append=True)

    def _calculate_adx(self, data: pd.DataFrame, period: int):
        final_col_name = f'ADX_{period}'

        # Ожидаемые сырые имена от pandas-ta
        raw_adx = f"ADX_{period}"
        raw_dmp = f"DMP_{period}"
        raw_dmn = f"DMN_{period}"

        target_cols = [raw_adx, raw_dmp, raw_dmn]

        # Удаляем строго их
        cols_to_drop = [c for c in target_cols if c in data.columns]
        if cols_to_drop:
            data.drop(columns=cols_to_drop, inplace=True)

        data.ta.adx(length=period, append=True)

        # Удаляем DMP и DMN, оставляем только ADX (согласно логике стратегий)
        # Если стратегии понадобятся DMP/DMN, это нужно будет менять здесь и в стратегии.
        # Пока чистим, чтобы не засорять память.
        for col in [raw_dmp, raw_dmn]:
            if col in data.columns:
                data.drop(columns=[col], inplace=True)