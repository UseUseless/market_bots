from abc import ABC, abstractmethod
from typing import Literal
from config import RISK_CONFIG

# Тип для выбора модели риск-менеджера
RiskManagerType = Literal["FIXED", "ATR"]


class BaseRiskManager(ABC):
    """Абстрактный базовый класс для всех менеджеров риска."""

    @abstractmethod
    def calculate_stop_loss(self, entry_price: float, direction: str) -> float:
        """Рассчитывает и возвращает абсолютный уровень стоп-лосса."""
        raise NotImplementedError

    @abstractmethod
    def calculate_take_profit(self, entry_price: float, direction: str, stop_loss_price: float) -> float:
        """Рассчитывает и возвращает абсолютный уровень тейк-профита."""
        raise NotImplementedError


class FixedRiskManager(BaseRiskManager):
    """
    Рассчитывает SL и TP на основе фиксированных процентов.
    """

    def calculate_stop_loss(self, entry_price: float, direction: str) -> float:
        risk_percent = RISK_CONFIG["DEFAULT_RISK_PERCENT_LONG"] if direction == 'BUY' else RISK_CONFIG["DEFAULT_RISK_PERCENT_SHORT"]
        sl_percent = risk_percent / 100.0
        return entry_price * (1 - sl_percent) if direction == 'BUY' else entry_price * (1 + sl_percent)

    def calculate_take_profit(self, entry_price: float, direction: str, stop_loss_price: float) -> float:
        risk_per_share = abs(entry_price - stop_loss_price)
        tp_ratio = 2.0 # Todo: Можно вынести в конфиг
        return entry_price + (risk_per_share * tp_ratio) if direction == 'BUY' else entry_price - (risk_per_share * tp_ratio)


class AtrRiskManager(BaseRiskManager):
    """
    Рассчитывает SL и TP на основе волатильности (ATR).
    """

    def __init__(self, atr_value: float):
        if not atr_value or atr_value <= 0:
            raise ValueError("ATR value must be positive.")
        self.atr_value = atr_value
        self.sl_multiplier = RISK_CONFIG["ATR_MULTIPLIER_SL"]
        self.tp_multiplier = RISK_CONFIG["ATR_MULTIPLIER_TP"]

    def calculate_stop_loss(self, entry_price: float, direction: str) -> float:
        sl_distance = self.atr_value * self.sl_multiplier
        return entry_price - sl_distance if direction == 'BUY' else entry_price + sl_distance

    def calculate_take_profit(self, entry_price: float, direction: str, stop_loss_price: float) -> float:
        # stop_loss_price здесь не используется, но нужен для совместимости интерфейса
        tp_distance = self.atr_value * self.tp_multiplier
        return entry_price + tp_distance if direction == 'BUY' else entry_price - tp_distance