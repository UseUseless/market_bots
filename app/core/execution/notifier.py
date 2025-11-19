import logging
from queue import Queue
import pandas as pd

from app.core.execution.abc import BaseExecutionHandler
from app.core.models.event import OrderEvent, FillEvent

try:
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
except ImportError:
    console = None

logger = logging.getLogger(__name__)


class NotifierExecutionHandler(BaseExecutionHandler):
    """
    Исполнитель для режима 'SIGNAL_ONLY'.
    Не отправляет ордера на биржу.
    Вместо этого он логирует сигналы и выводит красивые уведомления в консоль.
    В будущем здесь будет отправка в Telegram.
    """

    def __init__(self, events_queue: Queue):
        super().__init__(events_queue)

    def execute_order(self, event: OrderEvent, last_candle: pd.Series = None):
        """
        Получает OrderEvent (который прошел через RiskManager и готов к исполнению).
        Формирует уведомление.
        """
        # 1. Логируем факт сигнала
        logger.info(f"!!! СИГНАЛ !!! {event.direction} {event.instrument} | Qty: {event.quantity}")

        # 2. Красивый вывод в консоль (для пользователя)
        self._print_notification(event)

        # 3. Генерируем "Фейковый" FillEvent
        # Это нужно, чтобы PortfolioState обновился и мы "вошли" в позицию виртуально.
        # Если этого не сделать, RiskManager будет продолжать слать сигналы на вход на каждой свече.

        # Берем цену из price_hint (если есть) или просто логируем, что цена неизвестна (в LiveEngine мы это поправим)
        # В режиме сигналов мы предполагаем мгновенное исполнение по текущей цене
        fill_price = event.price_hint if event.price_hint else 0.0

        fake_fill = FillEvent(
            timestamp=event.timestamp,
            instrument=event.instrument,
            quantity=event.quantity,
            direction=event.direction,
            price=fill_price,
            commission=0.0,  # Комиссия 0 для сигналов
            trigger_reason=event.trigger_reason,
            stop_loss=event.stop_loss,
            take_profit=event.take_profit
        )

        # Возвращаем событие исполнения в очередь, чтобы Portfolio обновил свое состояние
        self.events_queue.put(fake_fill)

    def _print_notification(self, event: OrderEvent):
        """Форматирует и выводит сообщение."""
        color = "green" if event.direction == "BUY" else "red"
        emoji = "🚀" if event.direction == "BUY" else "🔻"

        msg = (
            f"{emoji} **СИГНАЛ: {event.direction}**\n"
            f"Инструмент: {event.instrument}\n"
            f"Тип: {event.trigger_reason}\n"
            f"Stop Loss: {event.stop_loss}\n"
            f"Take Profit: {event.take_profit}"
        )

        if console:
            console.print(Panel(msg, title="Market Bot Signal", style=f"bold {color}"))
        else:
            print(f"\n--- СИГНАЛ {event.direction} {event.instrument} ---\n{msg}\n")

    def stop(self):
        """Метод для остановки (не требуется для нотификатора, но нужен интерфейсу)"""
        pass