import asyncio
import logging
from typing import List, Set
from app.core.interfaces.abstract_publisher import IPublisher
from app.core.models.event import Event

logger = logging.getLogger(__name__)

class SignalBus(IPublisher):
    """
    Асинхронная шина событий (Pub/Sub).
    Позволяет отвязать производителей сигналов (Strategies) от потребителей (Telegram, DB).
    """
    def __init__(self):
        # Множество очередей подписчиков
        self._subscribers: Set[asyncio.Queue] = set()

    async def publish(self, event: Event):
        """
        Отправляет событие во все очереди подписчиков.
        """
        if not self._subscribers:
            return

        # Логируем только важные события, чтобы не спамить
        logger.debug(f"Bus: Publishing event {event}")

        # Рассылаем копию события каждому подписчику
        for q in self._subscribers:
            await q.put(event)

    def subscribe(self) -> asyncio.Queue:
        """
        Создает новую очередь для подписчика и регистрирует её.
        """
        q = asyncio.Queue()
        self._subscribers.add(q)
        logger.info("Bus: New subscriber registered.")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Удаляет подписчика."""
        if q in self._subscribers:
            self._subscribers.remove(q)