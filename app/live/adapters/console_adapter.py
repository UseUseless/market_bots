import asyncio
import logging
from app.live.bus.signal_bus import SignalBus
from app.core.models.event import SignalEvent

logger = logging.getLogger(__name__)

class ConsoleAdapter:
    """
    Простой слушатель, выводящий сигналы в stdout.
    """
    def __init__(self, bus: SignalBus):
        self.bus = bus
        self.queue = None

    async def start(self):
        self.queue = self.bus.subscribe()
        logger.info("ConsoleAdapter: Listening for signals...")

        while True:
            try:
                event = await self.queue.get()
                if isinstance(event, SignalEvent):
                    self._print_signal(event)
                self.queue.task_done()
            except asyncio.CancelledError:
                break

    def _print_signal(self, event: SignalEvent):
        """Красивый вывод."""
        direction_icon = "🟢 BUY" if event.direction == "BUY" else "🔴 SELL"
        print("\n" + "="*40)
        print(f" {direction_icon} | {event.instrument}")
        print(f" Strategy: {event.strategy_id}")
        print(f" Time: {event.timestamp}")
        print("="*40 + "\n")