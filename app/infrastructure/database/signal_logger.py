"""
Сервис логирования сигналов (Signal Logger).

Этот модуль содержит адаптер, который слушает шину событий (Event Bus)
и сохраняет все торговые сигналы (`SignalEvent`) в базу данных.
Это необходимо для формирования истории торговли, аналитики и отображения
ленты сигналов в пользовательском интерфейсе (Dashboard).
"""

import asyncio
import logging
from typing import Optional

from app.core.event_bus import SignalBus
from app.shared.events import SignalEvent
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import SignalRepository

logger = logging.getLogger(__name__)


class DBLoggerAdapter:
    """
    Асинхронный логгер сигналов в БД.

    Работает как фоновый подписчик (Consumer) шины событий.
    Гарантирует, что сигналы не только отправляются в Telegram, но и
    оседают в истории для последующего анализа.

    Attributes:
        bus (SignalBus): Ссылка на шину событий.
        queue (asyncio.Queue): Очередь, в которую шина дублирует события для этого логгера.
    """

    def __init__(self, bus: SignalBus):
        """
        Инициализирует адаптер.

        Args:
            bus (SignalBus): Глобальная шина событий.
        """
        self.bus = bus
        self.queue: Optional[asyncio.Queue] = None

    async def start(self):
        """
        Запускает бесконечный цикл обработки событий.

        Метод подписывается на обновления шины и обрабатывает входящие
        события по мере их поступления. Работает до тех пор, пока
        задача не будет отменена (CancelledError).
        """
        self.queue = self.bus.subscribe()
        logger.info("DBLogger: Слушатель запущен и готов к записи...")

        while True:
            try:
                # Ожидание события из очереди
                event = await self.queue.get()

                # Фильтруем только сигналы (другие события нас здесь не интересуют)
                if isinstance(event, SignalEvent):
                    await self._save_signal(event)

                # Сообщаем очереди, что задача обработана
                self.queue.task_done()

            except asyncio.CancelledError:
                # Штатное завершение работы при остановке приложения
                logger.info("DBLogger: Остановка слушателя...")
                break
            except Exception as e:
                # Ловим любые непредвиденные ошибки в цикле, чтобы сервис не упал
                logger.error(f"DBLogger: Ошибка в цикле обработки: {e}", exc_info=True)

    async def _save_signal(self, event: SignalEvent):
        """
        Сохраняет один сигнал в базу данных.

        Создает новую изолированную сессию БД для каждой операции записи,
        чтобы гарантировать атомарность и избежать проблем с долгими сессиями.

        Args:
            event (SignalEvent): Событие сигнала для сохранения.
        """
        try:
            async with async_session_factory() as session:
                repo = SignalRepository(session)
                await repo.log_signal(event)
                # logger.debug(f"Signal saved to DB: {event.instrument}")
        except Exception as e:
            logger.error(f"DBLogger: Не удалось сохранить сигнал в БД: {e}", exc_info=True)