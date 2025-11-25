from typing import Dict, List, Set, Any
from app.core.models.position import Position

class PortfolioState:
    """
    Класс-хранилище, инкапсулирующий все динамическое состояние портфеля.

    Этот объект не содержит бизнес-логики. Его единственная задача — быть
    единым источником правды о текущем капитале, позициях и сделках.
    Сервисы (RiskMonitor, OrderManager, FillProcessor) будут получать этот
    объект и модифицировать его.
    """

    def __init__(self, initial_capital: float):
        """
        Инициализирует состояние портфеля.

        :param initial_capital: Начальный капитал для симуляции или торговли.
        """
        # --- Финансовое состояние ---
        self.initial_capital: float = initial_capital
        """Начальный капитал, не изменяется в ходе работы."""

        self.current_capital: float = initial_capital
        """Текущий капитал, обновляется после каждой закрытой сделки."""

        # --- Состояние позиций и ордеров ---
        self.positions: Dict[str, Position] = {}
        """
        Словарь для хранения всех активных позиций.
        Ключ - тикер инструмента (str), значение - объект Position.
        """

        self.pending_orders: Set[str] = set()
        """
        Множество (set) для хранения тикеров инструментов, по которым был
        отправлен ордер, но еще не пришел отчет об исполнении (FillEvent).
        Это критически важный механизм для предотвращения отправки дублирующих
        ордеров по одному и тому же инструменту.
        """

        # --- История ---
        self.closed_trades: List[Dict[str, Any]] = []
        """
        Список словарей, содержащий информацию о всех закрытых сделках
        для финального отчета.
        """

    @property
    def frozen_capital(self) -> float:
        """
        Рассчитывает суммарную стоимость всех открытых позиций по ценам входа.
        Это капитал, который "заморожен" в рынке.
        """
        return sum(pos.quantity * pos.entry_price for pos in self.positions.values())

    @property
    def available_capital(self) -> float:
        """
        Рассчитывает капитал, доступный для открытия новых позиций.
        """
        # ВАЖНО: Мы отнимаем "замороженный" капитал от ОБЩЕГО текущего капитала.
        # Для простоты спотовой торговли это работает. Для маржинальной торговли
        # здесь была бы более сложная логика с учетом плеча и маржинальных требований.
        return self.current_capital - self.frozen_capital

    def to_dict(self) -> Dict[str, Any]:
        """
        Сериализация состояния. Нужна для сохранения чекпоинтов
        или передачи состояния в ML-модель.
        """
        return {
            "current_capital": self.current_capital,
            "positions_count": len(self.positions),
            "frozen_capital": self.frozen_capital,
            "available_capital": self.available_capital,
            "pending_orders": list(self.pending_orders)
        }