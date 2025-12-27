"""
Базовый класс стратегий (Strategy Interface).

Определяет каркас для всех торговых алгоритмов в системе.
Обеспечивает унифицированный интерфейс для работы как в режиме бэктеста, так и в реальном времени.

Реализована гибридная схема работы с данными.
В Backtest-режиме стратегия использует предрасчитанные данные (быстро).
В Live-режиме стратегия сама рассчитывает индикаторы "на лету" (безопасно, без Race Condition).
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
    Абстрактный родительский класс для всех торговых стратегий.

    Предоставляет методы для инициализации, расчета индикаторов и обработки свечей.
    Пользовательские стратегии должны наследовать этот класс и реализовывать `_calculate_signals`.

    Attributes:
        params_config (Dict[str, Dict]): Конфигурация параметров для оптимизации (Optuna).
        required_indicators (List[Dict]): Список необходимых индикаторов (SMA, RSI и т.д.).
        min_history_needed (int): Минимальное кол-во свечей для корректного расчета индикаторов.
        events_queue (Queue): Очередь для отправки сигналов.
        config (TradingConfig): Конфигурация сессии.
    """

    # Дефолтные значения (переопределяются в наследниках)
    params_config: Dict[str, Dict[str, Any]] = {}
    required_indicators: List[Dict[str, Any]] = []
    min_history_needed: int = 1

    def __init__(self, events_queue: Queue, config: TradingConfig):
        """
        Инициализирует стратегию.

        Args:
            events_queue (Queue): Очередь для отправки торговых сигналов (SignalEvent).
            config (TradingConfig): Полная конфигурация сессии (параметры, инструмент).
        """
        self.events_queue = events_queue
        self.config = config

        # Распаковка часто используемых полей для удобства доступа в наследниках
        self.instrument: str = config.instrument
        self.name: str = config.strategy_name
        self.params: Dict[str, Any] = config.strategy_params

        # Инициализация движка индикаторов (Composition)
        self.feature_engine = FeatureEngine()

        # Динамическое расширение требований к данным на основе настроек риска
        self._add_risk_manager_requirements()

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        """
        Собирает стандартные параметры из конфигурации всех родительских классов.

        Returns:
            Dict[str, Any]: Словарь дефолтных параметров стратегии.
        """
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    def _add_risk_manager_requirements(self):
        """
        Проверяет настройки Риск-Менеджера и добавляет необходимые индикаторы (например, ATR).
        """
        risk_cfg = self.config.risk_config
        risk_type = risk_cfg.get("type", "FIXED")

        if risk_type == "ATR":
            atr_period = risk_cfg.get("atr_period", 14)
            atr_req = {"name": "atr", "params": {"length": atr_period}}

            current_requirements = list(self.required_indicators)
            is_present = any(
                req.get('name') == 'atr' and req.get('params', {}).get('length') == atr_period
                for req in current_requirements
            )

            if not is_present:
                current_requirements.append(atr_req)
                self.required_indicators = current_requirements

    def process_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Выполняет полный цикл подготовки данных: расчет индикаторов и очистка.

        Используется для начальной подготовки в бэктесте или для локального
        расчета в Live-режиме.

        Args:
            data (pd.DataFrame): Сырые исторические данные (OHLCV).

        Returns:
            pd.DataFrame: Обогащенный DataFrame без пропусков (NaN), готовый к работе.
        """
        if data.empty:
            return data

        original_columns = set(data.columns)

        # 1. Расчет стандартных индикаторов
        enriched_data = self.feature_engine.add_required_features(
            data, self.required_indicators
        )

        # 2. Для кастомных фичей
        final_data = self._prepare_custom_features(enriched_data)

        # 3. Умная очистка (Smart Dropna)
        new_columns = list(set(final_data.columns) - original_columns)
        if new_columns:
            valid_indicators = [col for col in new_columns if not final_data[col].isna().all()]
            
            if valid_indicators:
                final_data.dropna(subset=valid_indicators, inplace=True)

        final_data.reset_index(drop=True, inplace=True)
        return final_data

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Метод для расчета специфических признаков, не входящих в стандартный набор.

        Args:
            data (pd.DataFrame): DataFrame с базовыми индикаторами.

        Returns:
            pd.DataFrame: DataFrame с добавленными кастомными колонками.
        """
        return data

    def on_candle(self, feed: MarketDataProvider):
        """
        Основной обработчик события "Новая свеча".

        Реализует адаптивную логику работы с данными:
        1. В Backtest: использует уже готовые индикаторы из фида.
        2. В Live: если индикаторов нет, рассчитывает их локально на копии данных.

        Args:
            feed (MarketDataProvider): Провайдер рыночных данных.
        """
        # Запрашиваем историю с запасом
        # +5 нужно, чтобы после dropna (из-за индикаторов) у нас осталось хотя бы 2 свечи
        history_needed = self.min_history_needed + 5
        
        # Получаем КОПИЮ данных (гарантируется обновленным провайдером)
        history_df = feed.get_history(length=history_needed)

        if len(history_df) < 2:
            return

        # --- Проверка: нужно ли считать индикаторы? ---
        # Если в данных только OHLCV, значит мы в Live режиме и нужно считать локально.
        needs_calculation = False
        if self.required_indicators:
            # Упрощенная эвристика: если колонок мало (<= 6), значит это "сырой" DF.
            if len(history_df.columns) <= 6: # OHLCV + Time
                needs_calculation = True

        if needs_calculation:
            # [Live Mode] Локальный расчет на копии данных
            history_df = self.process_data(history_df)

        # После расчета (и возможного dropna) данных может стать меньше
        if len(history_df) < 2:
            return

        # Извлекаем две последние свечи для анализа
        last_candle = history_df.iloc[-1]
        prev_candle = history_df.iloc[-2]

        # Получаем timestamp
        timestamp = last_candle.get('time', last_candle.name)

        self._calculate_signals(prev_candle, last_candle, timestamp)

    @abstractmethod
    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Ядро торговой логики. Определяет условия входа и выхода.

        Args:
            prev_candle (pd.Series): Данные предыдущей закрытой свечи.
            last_candle (pd.Series): Данные текущей закрытой свечи.
            timestamp (pd.Timestamp): Время закрытия текущей свечи.
        """
        raise NotImplementedError("Метод _calculate_signals должен быть реализован в стратегии.")