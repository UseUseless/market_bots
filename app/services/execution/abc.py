from abc import ABC, abstractmethod
from queue import Queue
import pandas as pd

from app.core.models.event import OrderEvent

class BaseExecutionHandler(ABC):
    """
    Абстрактный базовый класс для всех исполнителей ордеров.
    Определяет единый интерфейс для симулятора и реального исполнителя.
    """
    def __init__(self, events_queue: Queue):
        self.events_queue = events_queue

    @abstractmethod
    def execute_order(self, event: OrderEvent, last_candle: pd.Series = None):
        """
        Основной метод, который принимает OrderEvent и должен в конечном итоге
        сгенерировать FillEvent.

        :param event: Событие с деталями ордера.
        :param last_candle: Последняя доступная свеча. Обязательна для симулятора,
                            но может не использоваться в live-режиме.
        """
        raise NotImplementedError("Метод execute_order должен быть реализован.")