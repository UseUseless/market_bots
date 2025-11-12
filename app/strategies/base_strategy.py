from queue import Queue
import pandas as pd
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from collections import deque

from app.core.event import MarketEvent
from app.core.feature_engine import FeatureEngine


class BaseStrategy(ABC):
    """
    Абстрактный базовый класс для всех торговых стратегий.
    Отвечает за ПОЛНУЮ подготовку своих данных и генерацию сигналов.
    """
    params_config: Dict[str, Dict[str, Any]] = {}
    required_indicators: List[Dict[str, Any]] = []
    min_history_needed: int = 1

    def __init__(self, events_queue: Queue, instrument: str, params: Dict[str, Any],
                 risk_manager_type: str = "FIXED", risk_manager_params: Optional[Dict[str, Any]] = None):
        self.events_queue = events_queue
        self.instrument: str = instrument
        self.name: str = self.__class__.__name__
        self.params = params

        self._add_risk_manager_requirements(risk_manager_type, risk_manager_params)

        # Мы используем `min_history_needed + 2`, чтобы гарантировать, что у нас всегда
        # будет как минимум две свечи (`prev_candle` и `last_candle`) для анализа,
        # как только буфер истории заполнится.
        # `maxlen` автоматически удалит самый старый элемент при добавлении нового,
        # когда размер будет превышен.
        self.data_history = deque(maxlen=self.min_history_needed + 2)
        self._required_cols = []

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        """
        Извлекает и возвращает словарь с параметрами по умолчанию
        из атрибута params_config.
        """
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    def _add_risk_manager_requirements(self, risk_manager_type: str, risk_manager_params: Optional[Dict[str, Any]]):
        """
        Проверяет тип риск-менеджера и добавляет необходимые индикаторы
        в список `required_indicators` экземпляра.
        """
        if risk_manager_type == "ATR":
            # Если параметры не переданы, используем пустой словарь
            _rm_params = risk_manager_params if risk_manager_params is not None else {}
            # Берем период ATR из параметров РМ, с фолбэком на 14
            atr_period = _rm_params.get("atr_period", 14)
            atr_requirement = {"name": "atr", "params": {"period": atr_period}}

            current_requirements = list(self.required_indicators)
            if not any(req.get('name') == 'atr' and req['params']['period'] == atr_period for req in current_requirements):
                current_requirements.append(atr_requirement)

            self.required_indicators = current_requirements

    def process_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        ЕДИНАЯ ТОЧКА ВХОДА для полной обработки данных.
        """
        feature_engine = FeatureEngine()
        enriched_data = feature_engine.add_required_features(data, self.required_indicators)
        final_data = self._prepare_custom_features(enriched_data)
        self._required_cols = self._get_required_columns()
        final_data.dropna(inplace=True)
        final_data.reset_index(drop=True, inplace=True)
        return final_data

    def _get_required_columns(self) -> List[str]:
        cols = []
        for indicator in self.required_indicators:
            name = indicator['name']
            params = indicator.get('params', {})
            period = params.get('period')
            if name in ['sma', 'ema']:
                col = params.get('column', 'close')
                prefix = name.upper()
                cols.append(f'{prefix}_{period}' if col == 'close' else f'{prefix}_{period}_{col}')
            elif name == 'atr':
                cols.append(f'ATR_{period}')
        return cols

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Метод-заглушка для уникальных индикаторов.
        Дочерние стратегии ПЕРЕОПРЕДЕЛЯЮТ его, если им нужно рассчитать что-то,
        чего нет в FeatureEngine (например, Z-Score).
        По умолчанию ничего не делает.
        """
        return data

    def on_market_event(self, event: MarketEvent):
        self.data_history.append(event.data)
        if len(self.data_history) < 2:
            return
        prev_candle = self.data_history[-2]
        last_candle = self.data_history[-1]
        for candle in [prev_candle, last_candle]:
            for col in self._required_cols:
                if col not in candle or pd.isna(candle[col]):
                    return
        self._calculate_signals(prev_candle, last_candle, event.timestamp)

    @abstractmethod
    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        raise NotImplementedError("Метод _calculate_signals должен быть реализован.")