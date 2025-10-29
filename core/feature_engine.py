import pandas as pd
import pandas_ta as ta
from config import RISK_CONFIG


class FeatureEngine:
    """
    Отвечает за добавление в DataFrame общих, не зависящих от конкретной
    стратегии, фичей (технических индикаторов).
    """

    def add_common_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Добавляет в DataFrame набор стандартных индикаторов.
        """

        # 1. Расчет ATR (Average True Range)
        # Этот индикатор нужен для модели расчета размера позиции AtrAdjustedRiskSizer.
        atr_period = RISK_CONFIG["ATR_PERIOD"]
        data.ta.atr(length=atr_period, append=True, col_names=(f'ATR_{atr_period}',))

        # 2. В будущем сюда можно добавить другие общие индикаторы,
        # которые могут быть полезны для многих стратегий (например, SMA_200 для определения тренда).
        # data.ta.sma(length=200, append=True, col_names=('SMA_200',))

        return data