from queue import Queue
import pandas as pd
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from core.event import MarketEvent
from core.feature_engine import FeatureEngine


class BaseStrategy(ABC):
    """
    Абстрактный базовый класс для всех торговых стратегий.
    Отвечает за ПОЛНУЮ подготовку своих данных и генерацию сигналов.
    """
    required_indicators: List[Dict[str, Any]] = []
    min_history_needed: int = 1

    def __init__(self, events_queue: Queue, instrument: str, strategy_config: Optional[Dict[str, Any]] = None,
                 risk_manager_type: str = "FIXED", risk_config: Optional[Dict[str, Any]] = None):
        self.events_queue = events_queue
        self.instrument: str = instrument
        self.name: str = self.__class__.__name__
        self.strategy_config = strategy_config if strategy_config is not None else {}

        self._add_risk_manager_requirements(risk_manager_type, risk_config)

        self.data_history = []
        self._required_cols = []

    def _add_risk_manager_requirements(self, risk_manager_type: str, risk_config: Optional[Dict[str, Any]]):
        """
        Проверяет тип риск-менеджера и добавляет необходимые индикаторы
        в список `required_indicators` класса.
        """
        if risk_manager_type == "ATR":
            _risk_config = risk_config if risk_config is not None else {}
            atr_period = _risk_config.get("ATR_PERIOD", 14)
            atr_requirement = {"name": "atr", "params": {"period": atr_period}}

            # Клонируем список, чтобы не изменять атрибут класса напрямую для всех экземпляров
            current_requirements = list(self.required_indicators)
            if not any(req.get('name') == 'atr' for req in current_requirements):
                current_requirements.append(atr_requirement)

            # Обновляем required_indicators для ДАННОГО ЭКЗЕМПЛЯРА
            self.required_indicators = current_requirements

    def process_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        ЕДИНАЯ ТОЧКА ВХОДА для полной обработки данных.
        1. Вызывает FeatureEngine для стандартных индикаторов.
        2. Вызывает _prepare_custom_features для уникальных индикаторов стратегии.
        3. Собирает список колонок для проверки на NaN.
        4. ОЧИЩАЕТ данные от NaN и сбрасывает индекс.
        """
        # 1. Добавляем стандартные индикаторы
        feature_engine = FeatureEngine()
        print(f"DEBUG: Стратегия '{self.name}' запрашивает индикаторы: {self.required_indicators}")
        enriched_data = feature_engine.add_required_features(data, self.required_indicators)

        # 2. Добавляем кастомные индикаторы (если они есть)
        final_data = self._prepare_custom_features(enriched_data)

        # 3. Собираем список всех колонок, которые нужно будет проверять на NaN
        self._required_cols = self._get_required_columns()

        # 4. Очищаем данные и сбрасываем индекс
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
        if len(self.data_history) > self.min_history_needed + 2:
            self.data_history.pop(0)
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