"""
Модуль управления портфелем (Portfolio Orchestrator).

Содержит класс `Portfolio`, который является центральным узлом ("Фасадом")
бизнес-логики ядра. Он связывает воедино обработку сигналов, управление рисками,
исполнение ордеров и учет сделок.

Сам класс не реализует сложную логику, а делегирует задачи специализированным сервисам,
обеспечивая правильный поток данных между ними.
"""

from queue import Queue
import pandas as pd
from typing import Dict
import logging

from app.shared.events import MarketEvent, SignalEvent, FillEvent, Event
from app.core.portfolio.state import PortfolioState

from app.core.risk.monitor import RiskMonitor
from app.core.execution.order_logic import OrderManager
from app.core.portfolio.accounting import FillProcessor

logger = logging.getLogger(__name__)


class Portfolio:
    """
    Фасад для управления состоянием портфеля и торговыми операциями.

    Оркестрирует работу специализированных компонентов:
    1.  Получает рыночные данные -> обновляет кэш цен -> запускает `RiskMonitor`.
    2.  Получает сигналы -> проверяет наличие данных -> передает в `OrderManager`.
    3.  Получает исполнения -> передает в `FillProcessor` для учета.

    Attributes:
        events_queue (Queue[Event]): Системная шина событий.
        state (PortfolioState): Хранилище текущего состояния (баланс, позиции).
        risk_monitor (RiskMonitor): Сервис проверки SL/TP.
        order_manager (OrderManager): Сервис создания ордеров.
        fill_processor (FillProcessor): Сервис учета сделок.
        last_market_data (Dict[str, pd.Series]): Кэш последних известных цен
            по инструментам. Необходим, чтобы при получении сигнала знать текущую цену.
    """

    def __init__(self,
                 events_queue: Queue[Event],
                 portfolio_state: PortfolioState,
                 risk_monitor: RiskMonitor,
                 order_manager: OrderManager,
                 fill_processor: FillProcessor):
        """
        Инициализирует менеджер портфеля с внедрением зависимостей.

        Args:
            events_queue (Queue[Event]): Очередь событий.
            portfolio_state (PortfolioState): Объект состояния.
            risk_monitor (RiskMonitor): Инстанс монитора рисков.
            order_manager (OrderManager): Инстанс менеджера ордеров.
            fill_processor (FillProcessor): Инстанс процессора исполнений.
        """
        self.events_queue = events_queue

        # 1. Стейт (Данные)
        self.state = portfolio_state

        # 2. Сервисы (Логика)
        self.risk_monitor = risk_monitor
        self.order_manager = order_manager
        self.fill_processor = fill_processor

        # 3. Кэш рынка
        self.last_market_data: Dict[str, pd.Series] = {}

    def update_market_price(self, event: MarketEvent):
        """
        Обрабатывает поступление новых рыночных данных.

        Обновляет локальный кэш цен (чтобы другие компоненты имели доступ к
        актуальной цене Close) и запускает мониторинг рисков.

        Args:
            event (MarketEvent): Событие с данными свечи.
        """
        self.last_market_data[event.instrument] = event.data

        # Запускаем пассивную защиту позиций (проверка SL/TP)
        self.risk_monitor.check_positions(event, self.state)

    def on_signal(self, event: SignalEvent):
        """
        Обрабатывает торговый сигнал от стратегии.

        Проверяет, есть ли у нас рыночные данные для этого инструмента,
        и делегирует создание ордера менеджеру.

        Args:
            event (SignalEvent): Сигнал на вход или выход.
        """
        last_candle = self.last_market_data.get(event.instrument)

        if last_candle is None:
            # Ситуация возможна на самом старте, если стратегия сгенерировала сигнал
            # до того, как Portfolio получил первый MarketEvent по этому тикеру.
            logger.warning(f"Нет рыночных данных для {event.instrument}. Сигнал пропущен.")
            return

        # Делегируем принятие решения об ордере
        self.order_manager.process_signal(event, self.state, last_candle)

    def on_fill(self, event: FillEvent):
        """
        Обрабатывает подтверждение исполнения ордера (Fill).

        Делегирует задачу обновления баланса и позиций "бухгалтеру".

        Args:
            event (FillEvent): Событие исполнения сделки.
        """
        self.fill_processor.process_fill(event, self.state)