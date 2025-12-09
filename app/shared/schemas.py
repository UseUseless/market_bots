"""
Модуль схем валидации данных (Schemas).

Содержит Pydantic-модели, которые используются для проверки структуры и типов данных.
Гарантирует, что в стратегию попадут только те настройки,
которые реально поддерживаются выбранной биржей (согласно config.py)
"""

from typing import Dict, Any
from pydantic import BaseModel, Field, ConfigDict, model_validator

from app.shared.config import config

class StrategyConfigModel(BaseModel):
    """
    Универсальная модель конфигурации для инициализации любой стратегии.

    Служит контрактом: гарантирует, что класс стратегии получит валидный набор
    параметров. Экземпляры модели неизменяемы (`frozen=True`).

    Attributes:
        strategy_name (str): Имя класса стратегии (например, 'SimpleSMACross').
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
    # один и тот же изменяемый словарь для всех экземпляров
    params: Dict[str, Any] = Field(default_factory=dict)

    risk_manager_type: str = "FIXED"
    risk_manager_params: Dict[str, Any] = Field(default_factory=dict)

    # Настройки Pydantic
    model_config = ConfigDict(frozen=True)  # Запрет на изменение полей после создания

    @model_validator(mode='after')
    def validate_exchange_interval(self) -> 'StrategyConfigModel':
        """
        Проверяет, поддерживает ли указанная биржа выбранный интервал.

        Использует `config.EXCHANGE_INTERVAL_MAPS` как единственный источник истины.
        """
        # 1. Получаем доступные интервалы для этой биржи из конфига
        # Если биржа неизвестна (нет в конфиге), выдаем ошибку
        available_intervals = config.EXCHANGE_INTERVAL_MAPS.get(self.exchange)

        if available_intervals is None:
            raise ValueError(f"Неизвестная биржа: '{self.exchange}'. "
                             f"Доступные: {list(config.EXCHANGE_INTERVAL_MAPS.keys())}")

        # 2. Проверяем наличие интервала в списке ключей
        if self.interval not in available_intervals:
            sorted_intervals = sorted(list(available_intervals.keys()))
            raise ValueError(
                f"Интервал '{self.interval}' не поддерживается биржей '{self.exchange}'.\n"
                f"Доступные варианты: {sorted_intervals}"
            )

        return self