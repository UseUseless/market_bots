from queue import Queue
import pandas as pd
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from app.core.calculations.indicators import FeatureEngine
from app.core.interfaces import IDataFeed
from app.shared.schemas import StrategyConfigModel


class BaseStrategy(ABC):
    """
    Абстрактный базовый класс для всех торговых стратегий.
    Теперь работает со строгой конфигурацией StrategyConfigModel.
    """
    params_config: Dict[str, Dict[str, Any]] = {}
    required_indicators: List[Dict[str, Any]] = []
    min_history_needed: int = 1

    def __init__(self,
                 events_queue: Queue,
                 feature_engine: FeatureEngine,
                 config: StrategyConfigModel):

        self.events_queue = events_queue
        self.feature_engine = feature_engine

        # Сохраняем полный конфиг
        self.config = config

        # Для обратной совместимости и удобства распаковываем часто используемые поля
        self.instrument: str = config.instrument
        self.params: Dict[str, Any] = config.params
        self.name: str = config.strategy_name

        self._prev_candle_cache: Optional[pd.Series] = None

        # Инициализация риск-менеджера из конфига
        self._add_risk_manager_requirements(
            config.risk_manager_type,
            config.risk_manager_params
        )

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    def _add_risk_manager_requirements(self, risk_manager_type: str, risk_manager_params: Dict[str, Any]):
        if risk_manager_type == "ATR":
            atr_period = risk_manager_params.get("atr_period", 14)
            atr_requirement = {"name": "atr", "params": {"period": atr_period}}

            current_requirements = list(self.required_indicators)
            if not any(req.get('name') == 'atr' and req['params']['period'] == atr_period for req in current_requirements):
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

    def on_candle(self, feed: IDataFeed):
        """
        Единая точка входа для Бэктеста и Лайва.
        Стратегия сама запрашивает нужную ей историю у фида.
        """
        # 1. Запрашиваем историю (Current + History)
        # Нам нужно минимум 2 свечи (предыдущая и текущая) для большинства стратегий
        history_needed = max(self.min_history_needed, 2)
        history_df = feed.get_history(length=history_needed)

        # Если данных маловато (например, старт бэктеста), пропускаем
        if len(history_df) < 2:
            return

        # 2. Извлекаем свечи
        # iloc[-1] - это "текущая" закрытая свеча (на которой мы принимаем решение)
        # iloc[-2] - это предыдущая свеча
        last_candle = history_df.iloc[-1]
        prev_candle = history_df.iloc[-2]

        # Timestamp берем из текущей свечи
        # В BacktestFeed это поле 'time', в Live тоже.
        timestamp = last_candle.get('time', last_candle.name)

        # 3. Запускаем логику сигналов
        # (В Live это будет обернуто в run_in_executor снаружи,
        #  но сам метод стратегии должен быть синхронным и чистым)
        self._calculate_signals(prev_candle, last_candle, timestamp)

    @abstractmethod
    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        raise NotImplementedError("Метод _calculate_signals должен быть реализован.")