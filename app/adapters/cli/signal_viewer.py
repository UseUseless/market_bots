"""
Консольный визуализатор сигналов.
"""
from app.shared.interfaces import SignalHandler
from app.shared.events import SignalEvent
from app.shared.primitives import TradeDirection
from datetime import datetime

class ConsoleSignalViewer(SignalHandler):
    async def handle_signal(self, event: SignalEvent) -> None:
        if event.direction == TradeDirection.BUY:
            icon, color = "🟢 BUY ", "\033[92m"
        else:
            icon, color = "🔴 SELL", "\033[91m"

        reset = "\033[0m"
        price_str = f"{event.price:.4f}"

        print("\n" + "=" * 50)
        # strategy_name теперь есть в событии
        print(f"{color} {icon} | {event.instrument} | {event.strategy_name} {reset}")
        print(f" 💵 Price: {price_str}")
        print(f" 🕒 Time:  {event.timestamp}")
        print("=" * 50 + "\n")