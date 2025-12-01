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
        Доступные средства для открытия новых позиций.

        Так как мы используем Cash-Based учет, current_capital уже уменьшен
        на сумму открытых позиций (в Accounting._handle_fill_open).
        Поэтому available равен current.

        Returns:
            float: Текущее количество денег на счету
        """
        return self.current_capital

    @property
    def total_equity(self) -> float:
        """
        Полная стоимость портфеля (Кэш + Стоимость позиций).
        Полезно для расчета метрик просадки.

        Returns:
            float: Текущее количество денег на счету+стоимость купленных инструментов
        """
        # Это упрощенная оценка (по цене входа), для точности нужно Mark-to-Market
        return self.current_capital + self.frozen_capital

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
            "initial_capital": self.initial_capital,
            "current_capital": self.current_capital,  # Чистый кэш
            "available_capital": self.available_capital,
            "frozen_capital": self.frozen_capital,  # Деньги в позициях
            "total_equity": self.total_equity,  # Общая стоимость (Cash + Frozen)
            "positions_count": len(self.positions),
            "pending_orders": list(self.pending_orders)
        }