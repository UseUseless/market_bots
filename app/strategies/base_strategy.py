"""
Базовый класс стратегий (Strategy Interface).

Определяет каркас для всех торговых алгоритмов в системе.
Обеспечивает унифицированный интерфейс для работы как в режиме бэктеста (векторный расчет),
так и в реальном времени (побарная обработка).

Основные механизмы:
1.  **Auto-Discovery:** Параметры (`params_config`) собираются автоматически.
2.  **Feature Injection:** Индикаторы рассчитываются централизованно через `FeatureEngine`.
3.  **Risk Awareness:** Автоматически подгружает индикаторы, необходимые Риск-Менеджеру (например, ATR).
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
            Формат: `{"param_name": {"type": "int", "low": 10, "high": 50, "default": 20}}`.
        required_indicators (List[Dict]): Список индикаторов, необходимых стратегии.
            Формат: `[{"name": "sma", "params": {"period": 20}}]`.
        min_history_needed (int): Минимальное количество свечей для корректного расчета индикаторов.
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
        Собирает дефолтные параметры из конфигурации всех родительских классов (MRO).

        Это позволяет наследовать параметры стратегий. Например, `StrategyB(StrategyA)`
        автоматически получит параметры `StrategyA`, если не переопределит их.

        Returns:
            Dict[str, Any]: Словарь {имя_параметра: дефолтное_значение}.
        """
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    def _add_risk_manager_requirements(self):
        """
        Анализирует конфигурацию риск-менеджера и добавляет необходимые индикаторы.

        Пример: Если выбран риск-менеджер 'ATR', стратегия автоматически добавляет
        расчет ATR в список `required_indicators`, даже если сама стратегия его не использует.
        """
        risk_cfg = self.config.risk_config
        risk_type = risk_cfg.get("type", "FIXED")

        if risk_type == "ATR":
            atr_period = risk_cfg.get("atr_period", 14)
            atr_req = {"name": "atr", "params": {"period": atr_period}}

            # Копируем список, чтобы не мутировать атрибут класса (shared state hazard)
            current_requirements = list(self.required_indicators)

            # Проверка на дубликаты: добавляем только если такого индикатора еще нет
            is_present = any(
                req.get('name') == 'atr' and req.get('params', {}).get('period') == atr_period
                for req in current_requirements
            )

            if not is_present:
                current_requirements.append(atr_req)
                self.required_indicators = current_requirements

    def process_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Выполняет подготовку данных (Feature Engineering) для бэктеста.

        Рассчитывает все индикаторы векторно (сразу для всей истории) и очищает
        результирующий DataFrame от NaN значений, которые возникают в начале истории
        из-за периодов индикаторов ("разогрев").

        Args:
            data (pd.DataFrame): Сырые исторические данные (OHLCV).

        Returns:
            pd.DataFrame: Обогащенные данные, готовые к симуляции.
        """
        original_columns = set(data.columns)

        # 1. Расчет стандартных индикаторов (через FeatureEngine)
        enriched_data = self.feature_engine.add_required_features(
            data, self.required_indicators
        )

        # 2. Хук для кастомных фичей (переопределяется в наследниках)
        final_data = self._prepare_custom_features(enriched_data)

        # 3. Умная очистка (Smart Dropna)
        # Удаляем строки с NaN только в *новых* колонках (индикаторах).
        # Это предотвращает удаление данных, если в сырых данных были пропуски (хотя их быть не должно).
        new_columns = list(set(final_data.columns) - original_columns)

        if new_columns:
            # Проверяем, есть ли колонки, полностью состоящие из NaN (ошибка расчета)
            valid_indicators = [col for col in new_columns if not final_data[col].isna().all()]
            broken_indicators = [col for col in new_columns if col not in valid_indicators]

            if broken_indicators:
                logger.warning(
                    f"⚠️ Strategy '{self.name}': Индикаторы {broken_indicators} не рассчитались (полностью NaN). "
                    "Проверьте параметры или входные данные."
                )

            # Удаляем строки "разогрева"
            if valid_indicators:
                final_data.dropna(subset=valid_indicators, inplace=True)

        final_data.reset_index(drop=True, inplace=True)
        return final_data

    def _prepare_custom_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Хук для расчета специфических признаков, которые сложно описать через конфиг.

        Например: Z-Score, синтетические спреды, паттерны свечей.
        Может быть переопределен в дочернем классе.

        Args:
            data (pd.DataFrame): Данные с уже рассчитанными стандартными индикаторами.

        Returns:
            pd.DataFrame: Данные с добавленными кастомными колонками.
        """
        return data

    def on_candle(self, feed: MarketDataProvider):
        """
        Основной обработчик события "Новая свеча".

        Вызывается движком (Live или Backtest) при закрытии очередной свечи.
        Запрашивает у провайдера необходимую историю и передает её в логику стратегии.

        Args:
            feed (MarketDataProvider): Интерфейс доступа к рыночным данным.
        """
        # Запрашиваем историю с запасом (нужно минимум 2 свечи для сравнения prev/curr)
        history_needed = max(self.min_history_needed, 2)
        history_df = feed.get_history(length=history_needed)

        # Защита от холодного старта
        if len(history_df) < 2:
            return

        # Извлекаем две последние свечи для анализа пересечений/изменений
        last_candle = history_df.iloc[-1]
        prev_candle = history_df.iloc[-2]

        # Получаем timestamp из индекса или колонки
        timestamp = last_candle.get('time', last_candle.name)

        self._calculate_signals(prev_candle, last_candle, timestamp)

    @abstractmethod
    def _calculate_signals(self, prev_candle: pd.Series, last_candle: pd.Series, timestamp: pd.Timestamp):
        """
        Ядро торговой логики.

        Здесь реализуются правила входа и выхода. Метод должен анализировать
        переданные свечи и помещать `SignalEvent` в `self.events_queue` при выполнении условий.

        Args:
            prev_candle (pd.Series): Данные предыдущей закрытой свечи.
            last_candle (pd.Series): Данные текущей закрытой свечи.
            timestamp (pd.Timestamp): Время закрытия текущей свечи.
        """
        raise NotImplementedError("Метод _calculate_signals должен быть реализован в стратегии.")