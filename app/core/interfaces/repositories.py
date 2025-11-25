from abc import ABC, abstractmethod
from app.core.models.portfolio_state import PortfolioState

class IPortfolioRepository(ABC):
    """
    Интерфейс для сохранения и загрузки состояния портфеля.
    Позволяет ядру не зависеть от SQLAlchemy.
    """

    @abstractmethod
    async def save_portfolio_state(self, config_id: int, state: PortfolioState) -> None:
        """Сохраняет текущее состояние портфеля в БД."""
        raise NotImplementedError

    @abstractmethod
    async def load_portfolio_state(self, config_id: int, initial_capital: float) -> PortfolioState:
        """
        Загружает состояние из БД.
        Если состояния нет — создает новое (пустое) с initial_capital.
        """
        raise NotImplementedError