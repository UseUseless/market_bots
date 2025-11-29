"""
Модуль шины событий (Event Bus).

Реализует механизм асинхронной передачи сообщений между компонентами системы
по паттерну Publisher-Subscriber (Издатель-Подписчик). Это позволяет снизить
связность кода (Loose Coupling): производители сигналов (стратегии) ничего не знают
о потребителях (Telegram, БД, дашборд).
"""

import asyncio
import logging
from typing import Set

from app.core.interfaces import IPublisher
from app.shared.events import Event

logger = logging.getLogger(__name__)


class SignalBus(IPublisher):
    """
    Асинхронная in-memory шина событий.

    Отвечает за маршрутизацию событий от источника ко всем заинтересованным
    подписчикам. Использует `asyncio.Queue` для буферизации сообщений.
    """

    def __init__(self):
        """
        Инициализирует шину с пустым набором подписчиков.
        """
        # Множество очередей. Используем Set, чтобы избежать дублирования
        # одной и той же очереди.
        self._subscribers: Set[asyncio.Queue] = set()

    async def publish(self, event: Event):
        """
        Отправляет (вещает) событие всем активным подписчикам.

        Метод работает по принципу "Fan-Out": создается копия ссылки на событие
        для каждого подписчика. Если подписчиков нет, событие просто игнорируется.

        Args:
            event (Event): Объект события (SignalEvent, MarketEvent и т.д.),
                           который нужно доставить.
        """
        if not self._subscribers:
            return

        # Логируем на уровне DEBUG, чтобы не засорять основные логи в продакшене,
        # но иметь возможность трассировки при отладке.
        logger.debug(f"Bus: Publishing event {event}")

        # Раскладываем событие по всем очередям
        for q in self._subscribers:
            # put_nowait нельзя использовать, так как очередь может быть полной.
            # await q.put гарантирует, что мы подождем освобождения места, если установлен maxsize.
            await q.put(event)

    def subscribe(self) -> asyncio.Queue:
        """
        Регистрирует нового подписчика.

        Создает новую очередь, добавляет её в список рассылки и возвращает
        вызывающему коду. Теперь все новые события будут попадать и в эту очередь.

        Returns:
            asyncio.Queue: Очередь, из которой подписчик должен читать события.
        """
        q = asyncio.Queue()
        self._subscribers.add(q)
        logger.info("Bus: New subscriber registered.")
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """
        Удаляет подписчика из списка рассылки.

        Важно вызывать этот метод при остановке компонента-потребителя,
        чтобы избежать утечки памяти (накопления событий в "мертвых" очередях).

        Args:
            q (asyncio.Queue): Очередь, которую нужно отключить от шины.
        """
        if q in self._subscribers:
            self._subscribers.remove(q)
            logger.info("Bus: Subscriber unregistered.")