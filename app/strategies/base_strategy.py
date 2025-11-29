"""
Базовый класс стратегий.

Определяет каркас для всех торговых алгоритмов. Реализует паттерн "Шаблонный метод" (Template Method):
базовый класс берет на себя инфраструктурные задачи (получение данных, расчет индикаторов,
работа с конфигом), а наследники реализуют только специфичную логику сигналов.
"""

from abc import ABC, abstractmethod
from queue import Queue
from typing import List, Dict, Any, Optional

import pandas as pd

from app.core.calculations.indicators import FeatureEngine
from app.core.interfaces import IDataFeed
from app.shared.schemas import StrategyConfigModel


class BaseStrategy(ABC):
    """
    Абстрактный родительский класс для торговых стратегий.

    Обеспечивает унифицированный интерфейс для работы как в режиме бэктеста,
    так и в реальном времени (Live).

    Attributes:
        params_config (Dict): Конфигурация параметров для оптимизации (Optuna).
            Определяет типы, диапазоны и шаги перебора параметров.
            Пример: `{"sma_period": {"type": "int", "low": 10, "high": 50}}`.
        required_indicators (List[Dict]): Список индикаторов, необходимых стратегии.
            Автоматически рассчитываются `FeatureEngine`.
            Пример: `[{"name": "sma", "params": {"period": 20}}]`.
        min_history_needed (int): Минимальное количество свечей, необходимых для расчета
            индикаторов (warm-up period).
    """

    # Дефолтные значения (переопределяются в наследниках)
    params_config: Dict[str, Dict[str, Any]] = {}
    required_indicators: List[Dict[str, Any]] = []
    min_history_needed: int = 1

    def __init__(self,
                 events_queue: Queue,
                 feature_engine: FeatureEngine,
                 config: StrategyConfigModel):
        """
        Инициализирует стратегию.

        Args:
            events_queue (Queue): Очередь для отправки сигналов (SignalEvent).
            feature_engine (FeatureEngine): Сервис расчета индикаторов.
            config (StrategyConfigModel): Полная конфигурация стратегии (параметры, инструмент и т.д.).
        """
        self.events_queue = events_queue
        self.feature_engine = feature_engine
        self.config = config

        # Распаковка часто используемых полей для удобства доступа в наследниках
        self.instrument: str = config.instrument
        self.params: Dict[str, Any] = config.params
        self.name: str = config.strategy_name

        # Автоматическое добавление индикаторов, необходимых для выбранного риск-менеджера
        # (например, ATR для ATRRiskManager)
        self._add_risk_manager_requirements(
            config.risk_manager_type,
            config.risk_manager_params
        )

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        """
        Собирает дефолтные параметры стратегии из `params_config` всех родительских классов.

        Returns:
            Dict[str, Any]: Словарь {имя_параметра: значение_по_умолчанию}.
        """
        config = {}
        # Проходим по MRO (Method Resolution Order) в обратном порядке,
        # чтобы параметры наследников перезаписывали параметры родителей.
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    def _add_risk_manager_requirements(self, risk_manager_type: str, risk_manager_params: Dict[str, Any]):
        """
        Динамически добавляет индикаторы риск-менеджера в список требований стратегии.
        """
        if risk_manager_type == "ATR":
            atr_period = risk_manager_params.get("atr_period", 14)
            atr_requirement = {"name": "atr", "params": {"period": atr_period}}

            # Проверяем, нет ли уже такого требования, чтобы не дублировать
            current_requirements = list(self.required_indicators)
            is_present = any(
                req.get('name') == 'atr' and req.get('params', {}).get('period') == atr_period
                for req in current_requirements
            )

            if not is_present:
                current_requirements.append(atr_requirement)
                self.required_indicators = current_requirements

    def process_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Подготавливает исторические данные (Векторный режим).

        Используется ТОЛЬКО при инициализации бэктеста для предварительного расчета
        всех индикаторов сразу на всем датасете.

        Args:
            data (pd.DataFrame): Сырые данные OHLCV.

        Returns:
            pd.DataFrame: Обогащенный данными DataFrame (без NaN в начале).
        """
        original_columns = set(data.columns)

        # 1. Расчет стандартных индикаторов через FeatureEngine
        enriched_data = self.feature_engine.add_required_features(data, self.required_indicators)

        # 2. Расчет специфичных для стратегии фичей (хук для наследников)
        final_data = self._prepare_custom_features(enriched_data)

        # 3. Удаление строк с NaN, которые появились из-за лагов индикаторов (Warm-up)
        current_columns = set(final_data.columns)
        new_columns = list(current_columns - original_columns)

        if new_columns:
            final_data.dropna(subset=new_columns, inplace=True)

        final_data.reset_index(drop=True, inplace=True)

        return final_data

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Хук для расчета кастомных индикаторов, которые не поддерживаются FeatureEngine.
        Может быть переопределен в наследниках (например, для Z-Score).
        """
        return data

    def on_candle(self, feed: IDataFeed):
        """
        Обработчик новой свечи (Итеративный режим).

        Вызывается движком (LiveEngine или BacktestLoop) при закрытии каждой свечи.
        Запрашивает историю, извлекает текущую и предыдущую свечи и запускает логику сигналов.

        Args:
            feed (IDataFeed): Источник данных, предоставляющий доступ к истории.
        """
        # 1. Запрашиваем историю. Берем с запасом, чтобы хватило на расчеты.
        # В Live-режиме индикаторы уже посчитаны в Feed, но стратегии часто
        # нужно сравнить текущее значение с предыдущим (кроссовер).
        history_needed = max(self.min_history_needed, 2)
        history_df = feed.get_history(length=history_needed)

        # Если данных недостаточно (например, самое начало работы), пропускаем такт.
        if len(history_df) < 2:
            return

        # 2. Извлекаем ключевые свечи
        # .iloc[-1] -> Текущая только что закрытая свеча (на основе которой принимаем решение)
        # .iloc[-2] -> Предыдущая свеча (для определения пересечений/трендов)
        last_candle = history_df.iloc[-1]
        prev_candle = history_df.iloc[-2]

        timestamp = last_candle.get('time', last_candle.name)

        # 3. Делегируем принятие решения конкретной стратегии
        self._calculate_signals(prev_candle, last_candle, timestamp)

    @abstractmethod
    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Ядро торговой логики.

        Должен быть реализован в каждой стратегии. Анализирует свечи и, при выполнении
        условий, кладет `SignalEvent` в `self.events_queue`.

        Args:
            prev_candle (pd.Series): Предыдущая свеча (t-1).
            last_candle (pd.Series): Текущая закрытая свеча (t).
            timestamp (pd.Timestamp): Время текущей свечи.
        """
        raise NotImplementedError("Метод _calculate_signals должен быть реализован.")