"""
Модуль состояния портфеля.

Содержит класс `PortfolioState`, который является "Единым источником истины"
о текущих деньгах, позициях и активных ордерах. Этот объект передается
между компонентами ядра (RiskManager, OrderManager), позволяя им принимать
решения на основе актуальных данных.
"""

from typing import Dict, List, Set, Any
from app.shared.primitives import Position


class PortfolioState:
    """
    Хранилище динамического состояния портфеля.

    Этот класс не содержит сложной бизнес-логики (торговых правил).
    Его задача — хранить данные и предоставлять базовые расчеты (например,
    сколько денег доступно для торговли).

    Attributes:
        initial_capital (float): Стартовый капитал (не меняется).
        current_capital (float): Текущий полный капитал (Equity).
                                 Включает свободные деньги + стоимость позиций (в упрощенной модели).
                                 Обновляется после закрытия сделок.
        positions (Dict[str, Position]): Словарь открытых позиций.
                                         Ключ: Тикер инструмента. Значение: Объект Position.
        pending_orders (Set[str]): Множество тикеров, по которым отправлен ордер,
                                   но еще не пришло подтверждение (FillEvent).
                                   Используется для блокировки повторных сигналов.
        closed_trades (List[Dict[str, Any]]): История закрытых сделок (для отчетов).
    """

    def __init__(self, initial_capital: float):
        """
        Инициализирует пустое состояние портфеля.

        Args:
            initial_capital (float): Сумма средств на начало торговли.
        """
        self.initial_capital: float = initial_capital
        self.current_capital: float = initial_capital

        self.positions: Dict[str, Position] = {}
        self.pending_orders: Set[str] = set()
        self.closed_trades: List[Dict[str, Any]] = []

    @property
    def frozen_capital(self) -> float:
        """
        Рассчитывает капитал, "замороженный" в открытых позициях.

        Для спотового рынка и простых фьючерсов считается как сумма
        входов (Quantity * Entry Price).

        Returns:
            float: Сумма стоимости всех открытых позиций.
        """
        return sum(pos.quantity * pos.entry_price for pos in self.positions.values())

    @property
    def available_capital(self) -> float:
        """
        Рассчитывает свободный капитал (Buying Power).

        Используется Риск-менеджером для проверки, хватает ли денег
        на открытие новой позиции.

        Returns:
            float: Текущий капитал минус стоимость открытых позиций.
        """
        return self.current_capital - self.frozen_capital

    def to_dict(self) -> Dict[str, Any]:
        """
        Сериализует состояние в словарь.

        Полезно для:
        1. Логирования текущего состояния.
        2. Передачи состояния в ML-модели (как фичи).
        3. Сохранения чекпоинтов в БД.

        Returns:
            Dict[str, Any]: Словарь с ключевыми метриками портфеля.
        """
        return {
            "current_capital": self.current_capital,
            "positions_count": len(self.positions),
            "frozen_capital": self.frozen_capital,
            "available_capital": self.available_capital,
            "pending_orders": list(self.pending_orders)
        }