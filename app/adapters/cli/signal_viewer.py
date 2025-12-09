"""
Консольный визуализатор сигналов (CLI Signal Viewer).

Адаптер вывода, который отображает торговые сигналы в стандартный вывод
с цветовым кодированием для удобства мониторинга.
"""

from app.shared.interfaces import SignalHandler
from app.shared.events import SignalEvent
from app.shared.time_helper import interval_to_timedelta, get_display_timezone
from app.shared.primitives import TradeDirection


class ConsoleSignalViewer(SignalHandler):
    """
    Визуализатор сигналов в консоли.
    """

    async def handle_signal(self, event: SignalEvent) -> None:
        """
        Форматирует и выводит информацию о сигнале в консоль.

        Рассчитывает время закрытия свечи, конвертирует часовой пояс
        и применяет цветовое выделение (Зеленый/Красный).

        Args:
            event (SignalEvent): Событие сигнала.
        """
        # 1. Расчет времени закрытия свечи
        duration = interval_to_timedelta(event.interval)
        close_time_utc = event.timestamp + duration

        # 2. Перевод в локальное время пользователя (из конфига)
        local_time = close_time_utc.astimezone(get_display_timezone())
        time_str = local_time.strftime('%H:%M:%S')

        price_str = f"{event.price:.4f}" if event.price else "Market"

        # 3. Настройка цветов (ANSI escape codes)
        if event.direction == TradeDirection.BUY:
            direction_icon = "🟢 BUY "
            color_code = "\033[92m"  # Ярко-зеленый
        else:
            direction_icon = "🔴 SELL"
            color_code = "\033[91m"  # Ярко-красный

        reset_code = "\033[0m" # Сброс цвета

        # 4. Вывод "карточки" сигнала
        # Используем print, так как это UI-элемент
        print("\n" + "=" * 50)
        print(f"{color_code} {direction_icon} | {event.instrument} ({event.interval}) {reset_code}")
        print(f" 💵 Цена (Close): {price_str}")
        print(f" 🧠 Стратегия:    {event.strategy_id}")
        print(f" 🕒 Свеча закрыта: {time_str} ({get_display_timezone().key})")
        print("=" * 50 + "\n")
