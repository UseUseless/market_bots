from queue import Queue
from abc import ABC, abstractmethod
from datetime import datetime
#import logging

from core.event import OrderEvent, FillEvent
#from utils.trade_client import TinkoffTrader

class ExecutionHandler(ABC):
    """
    Абстрактный базовый класс для всех исполнителей ордеров.
    Определяет единый интерфейс для симулятора и реального исполнителя.
    """
    def __init__(self, events_queue: Queue):
        self.events_queue = events_queue

    @abstractmethod
    def execute_order(self, event: OrderEvent):
        """
        Основной метод, который должен быть реализован в дочерних классах.
        Принимает OrderEvent и должен в конечном итоге сгенерировать FillEvent.
        """
        raise NotImplementedError("Метод execute_order должен быть реализован.")

class SimulatedExecutionHandler(ExecutionHandler):
    """
    Простой симулятор исполнения ордеров для бэктестинга.
    Мгновенно "исполняет" ордер по рынку, создавая FillEvent.
    Не моделирует проскальзывание или частичное исполнение.
    """
    def execute_order(self, event: OrderEvent):
        """
        Просто превращает OrderEvent в FillEvent.
        Цена и комиссия равны нулю, так как их расчет - это
        ответственность Portfolio, который знает последнюю рыночную цену.
        """
        fill_event = FillEvent(
            timestamp=datetime.utcnow(),
            figi=event.figi,
            quantity=event.quantity,
            direction=event.direction,
            price=0,      # Цена будет определена в Portfolio
            commission=0.0  # Комиссия будет рассчитана в Portfolio
        )
        self.events_queue.put(fill_event)

# --- Этот класс является заготовкой для будущей реализации live-торговли ---
# class TinkoffExecutionHandler(ExecutionHandler):
#     """
#     Исполняет ордера через Tinkoff Invest API.
#     """
#     def __init__(self, events_queue: Queue, trade_mode: str):
#         super().__init__(events_queue)
#         self.trader = TinkoffTrader(trade_mode=trade_mode)

#     def execute_order(self, event: OrderEvent):
#         """
#         Отправляет реальный рыночный ордер через API.
#         В реальной системе после этого нужно было бы слушать стрим сделок,
#         чтобы получить точную цену исполнения и сгенерировать FillEvent.
#         """
#         try:
#             order_result = self.trader.place_market_order(
#                 figi=event.figi,
#                 quantity=event.quantity,
#                 direction=event.direction
#             )
#             if order_result:
#                 logging.info(f"Ордер {order_result.order_id} отправлен на биржу.")
#                 # ПРИМЕЧАНИЕ: Здесь должна быть логика обработки ответа от биржи
#                 # и генерации FillEvent на основе реальных данных.
#                 # Для простоты, в асинхронной версии это будет реализовано
#                 # через подписку на orders_stream.
#         except Exception as e:
#             logging.error(f"Критическая ошибка при исполнении ордера: {e}")