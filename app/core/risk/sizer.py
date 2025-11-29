"""
Модуль расчета размера позиции (Position Sizing).

Отвечает за определение объема сделки (количества лотов/акций/монет) на основе
параметров риска, рассчитанных в `RiskManager`.

Этот модуль реализует принцип "Anti-Martingale" (в частности, Fixed Fractional):
размер позиции определяется так, чтобы при срабатывании Стоп-Лосса
потерять фиксированную сумму (или процент) от капитала.
"""

from abc import ABC, abstractmethod
from app.shared.primitives import TradeRiskProfile


class BasePositionSizer(ABC):
    """
    Абстрактный базовый класс для алгоритмов сайзинга.

    Определяет интерфейс для конвертации профиля риска (цены и суммы)
    в конкретный объем актива.
    """

    @abstractmethod
    def calculate_size(self, risk_profile: TradeRiskProfile) -> float:
        """
        Рассчитывает теоретический размер позиции.

        Args:
            risk_profile (TradeRiskProfile): Объект с параметрами сделки
                (риск на сделку, риск на единицу актива).

        Returns:
            float: Неокругленное количество актива (например, 150.5342).
                   Округление до шага лота происходит позже в `RulesValidator`.
        """
        raise NotImplementedError


class FixedRiskSizer(BasePositionSizer):
    """
    Классический алгоритм фиксированного риска (Fixed Risk Position Sizing).

    Логика: "Я хочу купить столько акций, чтобы если цена упадет до Стоп-Лосса,
    я потерял ровно X долларов".

    Формула:
        Quantity = Total_Risk_Amount / Risk_Per_Share
    """

    def calculate_size(self, risk_profile: TradeRiskProfile) -> float:
        """
        Выполняет расчет объема.

        Args:
            risk_profile (TradeRiskProfile): Профиль, содержащий:
                - `risk_amount`: Сколько денег мы готовы потерять всего (например, $100).
                - `risk_per_share`: Сколько денег мы теряем на 1 акции (например, $2).

        Returns:
            float: Рассчитанный объем (в примере выше: 100 / 2 = 50.0).
                   Возвращает 0.0, если риск на акцию некорректен (<= 0).
        """
        # Защита от деления на ноль.
        # Если `risk_per_share` <= 0, это значит, что цена входа равна стоп-лоссу
        # (риска нет или он отрицательный), что технически невозможно для открытия позиции.
        if risk_profile.risk_per_share <= 1e-9:
            return 0.0

        # Основная формула мани-менеджмента.
        # Делим общий "бюджет на ошибку" на "стоимость ошибки" для одной единицы.
        quantity = risk_profile.risk_amount / risk_profile.risk_per_share

        return quantity