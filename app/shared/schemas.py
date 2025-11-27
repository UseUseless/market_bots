from typing import Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict

class StrategyConfigModel(BaseModel):
    """
    Универсальная модель конфигурации для любой стратегии.
    Гарантирует, что стратегия получит валидные данные.
    """
    strategy_name: str
    instrument: str
    exchange: str
    interval: str

    # Параметры стратегии (например, sma_period, thresholds)
    params: Dict[str, Any] = Field(default_factory=dict)

    # Параметры риск-менеджмента
    risk_manager_type: str = "FIXED"
    risk_manager_params: Dict[str, Any] = Field(default_factory=dict)

    # Pydantic v2 config: запрет на изменение полей после создания
    model_config = ConfigDict(frozen=True)

    @field_validator('interval')
    @classmethod
    def validate_interval(cls, v: str) -> str:
        """Простая проверка формата интервала."""
        valid_suffixes = ['min', 'hour', 'day', 'week']
        if not any(v.endswith(s) for s in valid_suffixes):
            raise ValueError(f"Некорректный формат интервала: {v}")
        return v