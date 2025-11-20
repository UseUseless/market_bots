from abc import ABC, abstractmethod
from app.core.models.event import Event


class IPublisher(ABC):
    """
    Интерфейс для публикации событий (сигналов).
    Позволяет стратегии не знать, куда уйдет сигнал (в консоль или Телеграм).
    """

    @abstractmethod
    async def publish(self, event: Event):
        """Отправляет событие в шину."""
        raise NotImplementedError