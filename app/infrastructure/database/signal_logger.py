"""
Сервис логирования сигналов (Signal Logger).

Адаптер, который сохраняет историю всех сгенерированных сигналов
в базу данных для последующего анализа и отображения в Dashboard.
"""

import logging

from app.shared.interfaces import SignalHandler
from app.shared.events import SignalEvent
from app.infrastructure.database.session import async_session_factory
from app.infrastructure.database.repositories import SignalRepository

logger = logging.getLogger(__name__)


class DBSignalLogger(SignalHandler):
    """
    Логгер сигналов в базу данных.

    Работает асинхронно, создавая изолированную сессию БД для каждой операции записи.
    """

    async def handle_signal(self, event: SignalEvent) -> None:
        """
        Сохраняет сигнал в таблицу signal_logs.

        Args:
            event (SignalEvent): Событие сигнала.
        """
        try:
            async with async_session_factory() as session:
                repo = SignalRepository(session)
                await repo.log_signal(event)
        except Exception as e:
            logger.error(f"DBLogger: Не удалось сохранить сигнал в БД: {e}", exc_info=True)