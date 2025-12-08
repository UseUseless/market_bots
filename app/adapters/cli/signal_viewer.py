"""
Консольный визуализатор сигналов (CLI Signal Viewer).

Этот модуль реализует простой текстовый интерфейс для отображения торговых сигналов
в реальном времени. Он подписывается на шину событий и форматирует входящие
`SignalEvent` в цветные сообщения в терминале.

Роль в архитектуре:
    Адаптер представления (View Adapter). Преобразует внутренние события системы
    в человекочитаемый формат.
"""

import asyncio
import logging
from typing import Optional

from app.shared.time_helper import interval_to_timedelta, msk_timezone
from app.core.event_bus import SignalBus
from app.shared.events import SignalEvent
from app.shared.primitives import TradeDirection

logger = logging.getLogger(__name__)


class ConsoleAdapter:
    """
    Асинхронный слушатель шины событий для вывода сигналов в консоль.

    Использует ANSI-коды для цветового выделения направления сделки (BUY/SELL).
    Пересчитывает время события в локальное время пользователя (МСК).

    Attributes:
        bus (SignalBus): Ссылка на глобальную шину событий.
        queue (Optional[asyncio.Queue]): Очередь, в которую шина дублирует события.
    """

    def __init__(self, bus: SignalBus):
        """
        Инициализирует адаптер.

        Args:
            bus (SignalBus): Шина событий для подписки.
        """
        self.bus = bus
        self.queue: Optional[asyncio.Queue] = None

    async def start(self):
        """
        Запускает бесконечный цикл прослушивания и отображения событий.

        Метод подписывается на шину и блокируется в ожидании новых событий.
        Завершается корректно при получении `asyncio.CancelledError`.
        """
        self.queue = self.bus.subscribe()
        logger.info("ConsoleAdapter: Слушатель сигналов запущен...")

        while True:
            try:
                event = await self.queue.get()

                # Нас интересуют только торговые сигналы
                if isinstance(event, SignalEvent):
                    self._print_signal(event)

                self.queue.task_done()

            except asyncio.CancelledError:
                logger.info("ConsoleAdapter: Остановка слушателя.")
                break
            except Exception as e:
                logger.error(f"ConsoleAdapter: Ошибка отображения: {e}")

    def _print_signal(self, event: SignalEvent):
        """
        Форматирует и выводит информацию о сигнале в stdout.

        Рассчитывает время закрытия свечи (Timestamp + Interval), переводит его
        в московский часовой пояс и красит вывод в зеленый (BUY) или красный (SELL).

        Args:
            event (SignalEvent): Событие сигнала.
        """
        # 1. Считаем время закрытия свечи (сигнал приходит по Open Time, но логически это Close)
        # TODO: Убедиться, что event.interval корректно заполняется в стратегии
        duration = interval_to_timedelta(event.interval or "1min")
        close_time_utc = event.timestamp + duration

        # 2. Переводим в МСК для удобства пользователя
        msk_time = close_time_utc.astimezone(msk_timezone())
        time_str = msk_time.strftime('%H:%M:%S')

        price_str = f"{event.price:.4f}" if event.price else "Market"

        # 3. Настройка цветов (ANSI escape codes)
        if event.direction == TradeDirection.BUY:
            direction_icon = "🟢 BUY "
            color_code = "\033[92m"  # Ярко-зеленый
        else:
            direction_icon = "🔴 SELL"
            color_code = "\033[91m"  # Ярко-красный

        reset_code = "\033[0m"  # Сброс цвета

        # 4. Вывод "карточки" сигнала
        # Используем print, так как это UI-элемент, а не системный лог
        print("\n" + "=" * 50)
        print(f"{color_code} {direction_icon} | {event.instrument} ({event.interval}) {reset_code}")
        print(f" 💵 Цена (Close): {price_str}")
        print(f" 🧠 Стратегия:    {event.strategy_id}")
        print(f" 🕒 Свеча закрыта: {time_str} (МСК)")
        print("=" * 50 + "\n")