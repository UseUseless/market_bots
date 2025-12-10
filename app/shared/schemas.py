"""
Схемы конфигурации (Schemas).

Здесь определен единый класс конфигурации `TradingConfig`.
Он заменяет собой разрозненные словари настроек и передается
во все компоненты системы (Стратегия, Портфель, Риск).
"""

from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

class TradingConfig(BaseModel):
    """
    Единый паспорт торговой сессии.

    Содержит полную информацию, необходимую для запуска стратегии
    в любом режиме (Бэктест, Лайв, Оптимизация).
    """
    # --- 1. Контекст Запуска ---
    mode: Literal["BACKTEST", "LIVE", "OPTIMIZATION"]

    # --- 2. Рыночные данные ---
    exchange: str        # 'bybit', 'tinkoff'
    instrument: str      # 'BTCUSDT' (Renamed back from symbol for consistency)
    interval: str        # '1h', '5min'

    # --- 3. Стратегия ---
    strategy_name: str
    # Параметры стратегии.
    strategy_params: Dict[str, Any] = Field(default_factory=dict)

    # --- 4. Риск-менеджмент ---
    # Пример: {"type": "ATR", "atr_period": 14, "risk_per_trade": 1.0}
    # Пример: {"type": "FIXED", "stop_loss_pct": 2.0}
    risk_config: Dict[str, Any] = Field(default_factory=lambda: {"type": "FIXED"})

    # --- 5. Деньги и Бэктест ---
    initial_capital: float = 10000.0
    commission_rate: float = 0.001  # 0.1% по умолчанию

    # Даты для обрезки истории (только для Backtest/Optimization)
    start_date: Optional[str] = None
    end_date: Optional[str] = None