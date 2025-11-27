import asyncio
import logging
from app.shared.time_helper import parse_interval_to_timedelta, msk_timezone
from app.core.event_bus import SignalBus
from app.shared.events import SignalEvent
from app.shared.primitives import TradeDirection

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
        """Красивый вывод с временем ЗАКРЫТИЯ свечи в МСК."""

        # 1. Считаем время закрытия
        duration = parse_interval_to_timedelta(event.interval)
        close_time_utc = event.timestamp + duration

        # 2. Переводим в МСК
        msk_time = close_time_utc.astimezone(msk_timezone())
        time_str = msk_time.strftime('%H:%M:%S')

        price_str = f"{event.price:.4f}" if event.price else "Market"

        if event.direction == TradeDirection.BUY:
            direction_icon = "🟢 BUY "
            color_code = "\033[92m"
        else:
            direction_icon = "🔴 SELL"
            color_code = "\033[91m"

        reset_code = "\033[0m"

        print("\n" + "=" * 50)
        print(f"{color_code} {direction_icon} | {event.instrument} ({event.interval}) {reset_code}")
        print(f" 💵 Цена (Close): {price_str}")
        print(f" 🧠 Стратегия:    {event.strategy_id}")
        # Пишем явно, что это время закрытия
        print(f" 🕒 Свеча закрыта: {time_str} (МСК)")
        print("=" * 50 + "\n")