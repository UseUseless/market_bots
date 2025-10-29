from abc import ABC, abstractmethod
from config import RISK_CONFIG

class BasePositionSizer(ABC):
    """Абстрактный базовый класс для всех калькуляторов размера позиции."""

    @abstractmethod
    def calculate_size(self, capital: float, entry_price: float, stop_loss_price: float, direction: str) -> float:
        """Рассчитывает размер позиции в лотах/акциях."""
        raise NotImplementedError


class FixedRiskSizer(BasePositionSizer):
    """
    Рассчитывает размер позиции, чтобы риск (расстояние до стопа)
    составлял фиксированный процент от капитала.
    """

    def calculate_size(self, capital: float, entry_price: float, stop_loss_price: float, direction: str) -> float:
        risk_percent = RISK_CONFIG["DEFAULT_RISK_PERCENT_LONG"] if direction == "BUY" \
            else RISK_CONFIG["DEFAULT_RISK_PERCENT_SHORT"]

        risk_amount = capital * (risk_percent / 100.0)
        risk_per_share = abs(entry_price - stop_loss_price)

        if risk_per_share == 0:
            return 0.0

        quantity = risk_amount / risk_per_share
        return quantity
