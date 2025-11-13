import logging
from typing import Dict, Any

from app.core.models.event import FillEvent
from app.core.models.portfolio_state import PortfolioState
from app.core.models.position import Position
from app.utils.trade_recorder import log_trade
from app.strategies.base_strategy import BaseStrategy  # Нужен для доступа к имени
from app.core.risk.risk_manager import BaseRiskManager

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
                 strategy: BaseStrategy,
                 risk_manager: BaseRiskManager,
                 exchange: str,
                 interval: str):
        self.trade_log_file = trade_log_file
        self.strategy = strategy
        self.risk_manager = risk_manager  # Нужен для доступа к параметрам для логирования
        self.exchange = exchange
        self.interval = interval

    def process_fill(self, event: FillEvent, state: PortfolioState):
        """
        Главный метод. Обрабатывает исполнение ордера.
        """
        instrument = event.instrument

        # Убираем ордер из списка ожидающих, так как он исполнился
        if instrument in state.pending_orders:
            state.pending_orders.remove(instrument)

        position = state.positions.get(instrument)

        # --- Сценарий 1: Открытие НОВОЙ позиции ---
        if not position:
            self._handle_fill_open(event, state)
        # --- Сценарий 2: Закрытие СУЩЕСТВУЮЩЕЙ позиции ---
        else:
            self._handle_fill_close(event, state, position)

    def _handle_fill_open(self, event: FillEvent, state: PortfolioState):
        """Обрабатывает исполнение ордера на открытие позиции."""

        # Рассчитываем риск-профиль на основе ФАКТИЧЕСКОЙ цены входа
        risk_profile = self.risk_manager.calculate_risk_profile(
            entry_price=event.price,
            direction=event.direction,
            capital=state.current_capital,
            # ВАЖНО: last_candle здесь не нужен, т.к. SL/TP уже не зависят от ATR на момент входа,
            # а рассчитываются от фактической цены. Для ATR Risk Manager'а это допущение,
            # что ATR на момент расчета ордера и на момент исполнения почти не изменился.
            last_candle=None
        )

        # Создаем новый объект Position
        new_position = Position(
            instrument=event.instrument,
            quantity=event.quantity,
            entry_price=event.price,
            entry_timestamp=event.timestamp,
            direction=event.direction,
            stop_loss=risk_profile.stop_loss_price,
            take_profit=risk_profile.take_profit_price
        )

        state.positions[event.instrument] = new_position

        logger.info(
            f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {event.instrument} @ {event.price:.4f} | "
            f"SL: {new_position.stop_loss:.4f}, TP: {new_position.take_profit:.4f}"
        )

    def _handle_fill_close(self, event: FillEvent, state: PortfolioState, position: Position):
        """Обрабатывает исполнение ордера на закрытие позиции."""

        # Рассчитываем финальный PnL
        if position.direction == 'BUY':
            pnl = (event.price - position.entry_price) * event.quantity - event.commission
        else:  # Для шорта
            pnl = (position.entry_price - event.price) * event.quantity - event.commission

        # Обновляем текущий капитал
        state.current_capital += pnl

        # Логируем сделку
        log_trade(
            trade_log_file=self.trade_log_file,
            strategy_name=self.strategy.name,
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
            risk_manager=self.risk_manager.__class__.__name__  # Логируем имя класса РМ
        )

        # Добавляем данные в историю для финального анализа
        state.closed_trades.append({
            'pnl': pnl,
            'entry_timestamp_utc': position.entry_timestamp,
            'exit_timestamp_utc': event.timestamp
        })

        # Удаляем позицию из словаря активных
        del state.positions[event.instrument]

        logger.info(
            f"Позиция ЗАКРЫТА по причине '{event.trigger_reason}': {event.instrument}. "
            f"PnL: {pnl:.2f}. Капитал: {state.current_capital:.2f}"
        )