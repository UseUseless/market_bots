"""
Базовый класс стратегий.

Определяет каркас для всех торговых алгоритмов.
Интегрирован с новой системой конфигурации (TradingConfig).
"""

from abc import ABC, abstractmethod
from queue import Queue
from typing import List, Dict, Any
import logging

import pandas as pd

from app.core.calculations.indicators import FeatureEngine
from app.shared.interfaces import MarketDataProvider
from app.shared.schemas import TradingConfig

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """
    Родительский класс для торговых стратегий.

    Обеспечивает унифицированный интерфейс для работы как в режиме бэктеста,
    так и в реальном времени.

    Attributes:
        params_config (Dict): Конфигурация параметров для оптимизации (Optuna).
            Пример: `{"sma_period": {"type": "int", "low": 10, "high": 50}}`.
        required_indicators (List[Dict]): Список индикаторов, необходимых стратегии.
            Пример: `[{"name": "sma", "params": {"period": 20}}]`.
        min_history_needed (int): Минимальное количество свечей для разогрева.
    """

    # Дефолтные значения (переопределяются в наследниках)
    params_config: Dict[str, Dict[str, Any]] = {}
    required_indicators: List[Dict[str, Any]] = []
    min_history_needed: int = 1

    def __init__(self, events_queue: Queue, config: TradingConfig):
        """
        Инициализирует стратегию.

        Args:
            events_queue (Queue): Очередь для отправки сигналов.
            config (TradingConfig): Полная конфигурация сессии.
        """
        self.events_queue = events_queue
        self.config = config

        # Распаковка часто используемых полей
        self.instrument: str = config.instrument
        self.name: str = config.strategy_name
        self.params: Dict[str, Any] = config.strategy_params

        # Инициализируем движок индикаторов внутри (симплфикация)
        self.feature_engine = FeatureEngine()

        # Автоматическое добавление индикаторов для Риск-менеджера
        self._add_risk_manager_requirements()

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        """
        Собирает дефолтные параметры из конфигурации всех родительских классов.
        """
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    def _add_risk_manager_requirements(self):
        """
        Проверяет конфиг риска и добавляет ATR, если это нужно риск-менеджеру.
        """
        risk_cfg = self.config.risk_config
        risk_type = risk_cfg.get("type", "FIXED")

        if risk_type == "ATR":
            atr_period = risk_cfg.get("atr_period", 14)
            atr_req = {"name": "atr", "params": {"period": atr_period}}

            # Копируем список, чтобы не менять атрибут класса
            current_requirements = list(self.required_indicators)

            # Проверка на дубликаты
            is_present = any(
                req.get('name') == 'atr' and req.get('params', {}).get('period') == atr_period
                for req in current_requirements
            )

            if not is_present:
                current_requirements.append(atr_req)
                self.required_indicators = current_requirements

    def process_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Векторный расчет индикаторов (для бэктеста).
        """
        original_columns = set(data.columns)

        # 1. Расчет стандартных индикаторов
        enriched_data = self.feature_engine.add_required_features(
            data, self.required_indicators
        )

        # 2. Хук для кастомных фичей
        final_data = self._prepare_custom_features(enriched_data)

        # 3. Умная очистка NaN (Smart Dropna)
        new_columns = list(set(final_data.columns) - original_columns)

        if new_columns:
            valid_indicators = [col for col in new_columns if not final_data[col].isna().all()]
            broken_indicators = [col for col in new_columns if col not in valid_indicators]

            if broken_indicators:
                logger.warning(
                    f"⚠️ Strategy '{self.name}': Индикаторы {broken_indicators} пусты. "
                    "Проверьте историю данных."
                )

            if valid_indicators:
                final_data.dropna(subset=valid_indicators, inplace=True)

        final_data.reset_index(drop=True, inplace=True)
        return final_data

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """Хук для дочерних классов (Z-Score и т.п.)."""
        return data

    def on_candle(self, feed: MarketDataProvider):
        """
        Обработчик новой свечи. Запускает логику стратегии.
        """
        # Берем историю с запасом
        history_needed = max(self.min_history_needed, 2)
        history_df = feed.get_history(length=history_needed)

        if len(history_df) < 2:
            return

        last_candle = history_df.iloc[-1]
        prev_candle = history_df.iloc[-2]
        timestamp = last_candle.get('time', last_candle.name)

        self._calculate_signals(prev_candle, last_candle, timestamp)

    @abstractmethod
    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Ядро торговой логики. Реализуется в наследниках.
        """
        raise NotImplementedError