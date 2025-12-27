"""
Схемы конфигурации для передачи данных по системе.
"""

from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

RunModeType = Literal["BACKTEST", "LIVE", "OPTIMIZATION"]

class TradingConfig(BaseModel):
    """
    Данные торговой сессии.

    Содержит полную информацию, необходимую для запуска стратегии
    в любом режиме (Бэктест, Лайв, Оптимизация).
    Передается во все компоненты системы (Стратегия, Портфель, Риск).
    """
    # --- 1. Контекст Запуска ---
    mode: RunModeType

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
    slippage_config: Dict[str, Any] = Field(
        default_factory=lambda: {"ENABLED": False, "IMPACT_COEFFICIENT": 0.0}
        )

    # Даты для обрезки истории (только для Backtest/Optimization)
    start_date: Optional[str] = None
    end_date: Optional[str] = None