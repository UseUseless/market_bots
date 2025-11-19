from queue import Queue
import pandas as pd
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from app.core.models.event import MarketEvent
from app.core.services.feature_engine import FeatureEngine


class BaseStrategy(ABC):
    """
    Абстрактный базовый класс для всех торговых стратегий.
    Реализует принцип Open/Closed и гарантирует расчет кастомных фичей в Live.
    """
    params_config: Dict[str, Dict[str, Any]] = {}
    required_indicators: List[Dict[str, Any]] = []
    min_history_needed: int = 1

    def __init__(self, events_queue: Queue, instrument: str, params: Dict[str, Any],
                 feature_engine: FeatureEngine,
                 risk_manager_type: str = "FIXED", risk_manager_params: Optional[Dict[str, Any]] = None):

        self.events_queue = events_queue
        self.instrument: str = instrument
        self.name: str = self.__class__.__name__
        self.params = params
        self.feature_engine = feature_engine

        self._add_risk_manager_requirements(risk_manager_type, risk_manager_params)

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    def _add_risk_manager_requirements(self, risk_manager_type: str, risk_manager_params: Optional[Dict[str, Any]]):
        if risk_manager_type == "ATR":
            _rm_params = risk_manager_params if risk_manager_params is not None else {}
            atr_period = _rm_params.get("atr_period", 14)
            atr_requirement = {"name": "atr", "params": {"period": atr_period}}

            current_requirements = list(self.required_indicators)
            if not any(
                    req.get('name') == 'atr' and req['params']['period'] == atr_period for req in current_requirements):
                current_requirements.append(atr_requirement)
            self.required_indicators = current_requirements

    def process_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Используется ТОЛЬКО для Бэктеста.
        """
        original_columns = set(data.columns)

        # 1. Стандартные индикаторы
        enriched_data = self.feature_engine.add_required_features(data, self.required_indicators)

        # 2. Кастомные индикаторы
        final_data = self._prepare_custom_features(enriched_data)

        # 3. Чистка NaN
        current_columns = set(final_data.columns)
        new_columns = list(current_columns - original_columns)

        if new_columns:
            final_data.dropna(subset=new_columns, inplace=True)

        final_data.reset_index(drop=True, inplace=True)

        return final_data

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """Метод-заглушка для уникальных индикаторов (Z-Score и т.д.)."""
        return data

    def on_market_event(self, event: MarketEvent):
        """
        Обрабатывает приход данных в Live-режиме.
        """
        data_window = event.data

        if not isinstance(data_window, pd.DataFrame) or len(data_window) < 2:
            return

        data_window = self._prepare_custom_features(data_window)

        prev_candle = data_window.iloc[-2]
        last_candle = data_window.iloc[-1]

        self._calculate_signals(prev_candle, last_candle, event.timestamp)

    @abstractmethod
    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        raise NotImplementedError("Метод _calculate_signals должен быть реализован.")