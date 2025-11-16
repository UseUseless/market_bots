import logging
from typing import Dict, Any

from app.core.models.event import FillEvent
from app.core.models.portfolio_state import PortfolioState
from app.core.models.position import Position
from app.utils.file_io import save_trade_log

logger = logging.getLogger(__name__)


class FillProcessor:
    """
    Сервис-бухгалтер. Обрабатывает фактическое исполнение ордеров (FillEvent).

    Отвечает за:
    - Обновление состояния портфеля (капитал, открытые/закрытые позиции).
    - Расчет PnL по закрытым сделкам.
    - Логирование завершенных сделок.
    """
    def __init__(self,
                 trade_log_file: str | None,
                 exchange: str,
                 interval: str,
                 strategy_name: str,
                 risk_manager_name: str,
                 risk_manager_params: Dict[str, Any]):
        """
        Инициализируется только необходимыми для логирования метаданными.

        :param trade_log_file: Путь к файлу для записи сделок.
        :param exchange: Название биржи.
        :param interval: Таймфрейм.
        :param strategy_name: Имя используемой стратегии.
        :param risk_manager_name: Имя класса используемого риск-менеджера.
        :param risk_manager_params: Параметры риск-менеджера.
        """
        self.trade_log_file = trade_log_file
        self.exchange = exchange
        self.interval = interval
        self.strategy_name = strategy_name
        self.risk_manager_name = risk_manager_name
        self.risk_manager_params = risk_manager_params

    def process_fill(self, event: FillEvent, state: PortfolioState):
        """
        Главный метод. Обрабатывает исполнение ордера.
        """
        instrument = event.instrument

        if instrument in state.pending_orders:
            state.pending_orders.remove(instrument)

        position = state.positions.get(instrument)

        if not position:
            self._handle_fill_open(event, state)
        else:
            self._handle_fill_close(event, state, position)

    def _handle_fill_open(self, event: FillEvent, state: PortfolioState):
        """Обрабатывает исполнение ордера на открытие позиции."""
        new_position = Position(
            instrument=event.instrument,
            quantity=event.quantity,
            entry_price=event.price,
            entry_timestamp=event.timestamp,
            direction=event.direction,
            stop_loss=event.stop_loss,
            take_profit=event.take_profit
        )

        state.positions[event.instrument] = new_position

        logger.info(
            f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {event.instrument} @ {event.price:.4f} | "
            f"SL: {new_position.stop_loss:.4f}, TP: {new_position.take_profit:.4f}"
        )

    def _handle_fill_close(self, event: FillEvent, state: PortfolioState, position: Position):
        """Обрабатывает исполнение ордера на закрытие позиции."""
        if position.direction == 'BUY':
            pnl = (event.price - position.entry_price) * event.quantity - event.commission
        else:
            pnl = (position.entry_price - event.price) * event.quantity - event.commission

        state.current_capital += pnl

        save_trade_log(
            trade_log_file=self.trade_log_file,
            strategy_name=self.strategy_name,
            exchange=self.exchange,
            instrument=event.instrument,
            direction=position.direction,
            entry_timestamp=position.entry_timestamp,
            exit_timestamp=event.timestamp,
            entry_price=position.entry_price,
            exit_price=event.price,
            pnl=pnl,
            exit_reason=event.trigger_reason,
            interval=self.interval,
            risk_manager=self.risk_manager_name
        )

        state.closed_trades.append({
            'pnl': pnl,
            'entry_timestamp_utc': position.entry_timestamp,
            'exit_timestamp_utc': event.timestamp
        })

        del state.positions[event.instrument]

        logger.info(
            f"Позиция ЗАКРЫТА по причине '{event.trigger_reason}': {event.instrument}. "
            f"PnL: {pnl:.2f}. Капитал: {state.current_capital:.2f}"
        )