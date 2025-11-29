"""
Модуль схем валидации данных (Schemas).

Содержит Pydantic-модели, которые используются для проверки структуры и типов данных,
поступающих из внешних источников (база данных, API, пользовательский ввод),
перед тем как передать их во внутренние компоненты системы (Стратегии).
"""

from typing import Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict


class StrategyConfigModel(BaseModel):
    """
    Универсальная модель конфигурации для инициализации любой стратегии.

    Служит контрактом: гарантирует, что класс стратегии получит валидный набор
    параметров, и избавляет стратегию от необходимости проверять ключи словарей вручную.
    Экземпляры модели неизменяемы (`frozen=True`).

    Attributes:
        strategy_name (str): Имя класса стратегии (например, 'SimpleSMACross').
                             Должно совпадать с ключом в `AVAILABLE_STRATEGIES`.
        instrument (str): Тикер инструмента (например, 'BTCUSDT').
        exchange (str): Название биржи (например, 'bybit').
        interval (str): Таймфрейм свечей (например, '1hour', '5min').
                        Проверяется валидатором на наличие корректного суффикса.
        params (Dict[str, Any]): Словарь специфичных параметров стратегии
                                 (периоды индикаторов, пороги и т.д.).
        risk_manager_type (str): Тип риск-менеджера (например, 'FIXED', 'ATR').
        risk_manager_params (Dict[str, Any]): Параметры для настройки риск-менеджера.
    """
    strategy_name: str
    instrument: str
    exchange: str
    interval: str

    # Field(default_factory=dict) используется, чтобы не создавать
    # один и тот же изменяемый словарь для всех экземпляров (mutable default argument trap)
    params: Dict[str, Any] = Field(default_factory=dict)

    risk_manager_type: str = "FIXED"
    risk_manager_params: Dict[str, Any] = Field(default_factory=dict)

    # Настройки Pydantic V2
    model_config = ConfigDict(frozen=True)  # Запрет на изменение полей после создания

    @field_validator('interval')
    @classmethod
    def validate_interval(cls, v: str) -> str:
        """
        Проверяет формат интервала на соответствие поддерживаемым суффиксам.

        Args:
            v (str): Строка интервала (например, '15min').

        Returns:
            str: Исходная строка, если проверка пройдена.

        Raises:
            ValueError: Если интервал имеет неизвестный суффикс.
        """
        valid_suffixes = ['min', 'hour', 'day', 'week', 'month']
        if not any(v.endswith(s) for s in valid_suffixes):
            raise ValueError(f"Некорректный формат интервала: '{v}'. Ожидаются суффиксы: {valid_suffixes}")
        return v