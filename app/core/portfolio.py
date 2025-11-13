from queue import Queue
import pandas as pd
from typing import Dict
import logging

# --- Модели данных ---
from app.core.models.event import MarketEvent, SignalEvent, FillEvent, Event
from app.core.models.portfolio_state import PortfolioState

# --- Сервисы ---
from app.core.services.risk_monitor import RiskMonitor
from app.core.services.order_manager import OrderManager
from app.core.services.fill_processor import FillProcessor

logger = logging.getLogger(__name__)

class Portfolio:
    """
    Класс-фасад, являющийся центральной точкой сборки и оркестрации ядра системы.

    Он не содержит сложной бизнес-логики. Его основные задачи:
    1. Владеть состоянием портфеля (PortfolioState).
    2. Хранить ссылки на специализированные сервисы (RiskMonitor, OrderManager, FillProcessor).
    3. Делегировать входящие события (MarketEvent, SignalEvent, FillEvent)
       соответствующим обработчикам.
    4. Хранить последнюю рыночную информацию (last_market_data) для передачи сервисам.
    """

    def __init__(self,
                 events_queue: Queue[Event],
                 initial_capital: float,
                 risk_monitor: RiskMonitor,
                 order_manager: OrderManager,
                 fill_processor: FillProcessor):
        """
        Инициализирует Portfolio, получая все зависимости извне (Dependency Injection).

        :param events_queue: Общая очередь событий.
        :param initial_capital: Начальный капитал.
        :param risk_monitor: Сервис для проверки SL/TP.
        :param order_manager: Сервис для создания ордеров из сигналов.
        :param fill_processor: Сервис для обработки исполненных ордеров.
        """
        self.events_queue = events_queue

        # 1. Инициализация состояния
        self.state = PortfolioState(initial_capital)

        # 2. Сохранение ссылок на сервисы-обработчики
        self.risk_monitor = risk_monitor
        self.order_manager = order_manager
        self.fill_processor = fill_processor

        # 3. Хранилище последних рыночных данных
        self.last_market_data: Dict[str, pd.Series] = {}

    def update_market_price(self, event: MarketEvent):
        """
        Вызывается на каждый MarketEvent.
        Обновляет последнюю известную цену и делегирует проверку рисков.
        """
        self.last_market_data[event.instrument] = event.data

        # Делегируем проверку SL/TP нашему специалисту
        self.risk_monitor.check_positions(event, self.state)

    def on_signal(self, event: SignalEvent):
        """
        Вызывается на каждый SignalEvent.
        Делегирует обработку сигнала менеджеру ордеров.
        """
        last_candle = self.last_market_data.get(event.instrument)
        if last_candle is None:
            # Логируем предупреждение, если для обработки сигнала нет рыночных данных
            # (может произойти в редких случаях на самых первых свечах)
            logger.warning(f"Нет рыночных данных для обработки сигнала по {event.instrument}, сигнал проигнорирован.")
            return

        # Делегируем создание ордера нашему специалисту
        self.order_manager.process_signal(event, self.state, last_candle)

    def on_fill(self, event: FillEvent):
        """
        Вызывается на каждый FillEvent.
        Делегирует обработку исполнения ордера нашему "бухгалтеру".
        """
        self.fill_processor.process_fill(event, self.state)