from abc import ABC, abstractmethod
from app.core.risk.risk_manager import TradeRiskProfile

class BasePositionSizer(ABC):
    """
    Абстрактный базовый класс для всех калькуляторов размера позиции.
    Его единственная задача - ответить на вопрос "Сколько лотов/акций покупать?",
    основываясь на уже готовом профиле риска.
    """
    @abstractmethod
    def calculate_size(self, risk_profile: TradeRiskProfile) -> float:
        """Рассчитывает размер позиции в лотах/акциях."""
        raise NotImplementedError


class FixedRiskSizer(BasePositionSizer):
    """
    Рассчитывает размер позиции на основе готового профиля риска.
    "FixedRisk" - рискуем фиксированной суммой от капитала,
    которая уже рассчитана и передана внутри risk_profile.
    """

    def calculate_size(self, risk_profile: TradeRiskProfile) -> float:
        # Защита от деления на ноль.
        # Это может произойти, если цена входа и стоп-лосса совпадут (например, из-за нулевого ATR).
        # В этом случае мы не можем открыть позицию, поэтому возвращаем 0.
        if risk_profile.risk_per_share == 0:
            return 0.0
        # (Общая сумма, которой я готов рискнуть) / (Сумма, которую я теряю на 1 акции)
        # = (Количество акций, которое я могу купить).
        # Вся логика по расчету этих двух величин инкапсулирована в RiskManager
        quantity = risk_profile.risk_amount / risk_profile.risk_per_share # (1) пояснение в risk_manager.py

        # Возвращаем количество. Оно может быть дробным (float),
        # т.к. округление до целого - это ответственность Portfolio,
        # который знает, с каким типом актива он работает (акции или крипта)
        return quantity