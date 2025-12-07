"""
Модуль учета сделок и расчета PnL (Accounting).

Этот компонент отвечает за "бухгалтерию" портфеля. Он реагирует на события
исполнения ордеров (`FillEvent`), обновляет состояние позиций, рассчитывает
прибыль/убыток и ведет журнал сделок.
"""

import logging
from typing import Dict, Any, Optional

from app.shared.events import FillEvent
from app.core.portfolio.state import PortfolioState
from app.infrastructure.storage.file_io import save_trade_log
from app.shared.primitives import TradeDirection, Position

logger = logging.getLogger(__name__)

# Точность для финансовых вычислений (баланс, PnL в котируемой валюте).
# 10 знаков достаточно для корректного учета USDT/RUB, не перегружая float.
PRECISION = 10


class FillProcessor:
    """
    Обработчик исполненных сделок (Accounting Engine).

    Выполняет две основные функции:
    1.  **Управление состоянием:** Создает объекты `Position` при входе и удаляет их при выходе.
    2.  **Финансовый учет:** Рассчитывает PnL (Profit and Loss), учитывает комиссии
        и обновляет баланс (`current_capital`) портфеля по модели Cash-Based.

    Attributes:
        trade_log_file (Optional[str]): Путь к файлу для сохранения истории сделок (csv/jsonl).
        exchange (str): Имя биржи (для метаданных лога).
        interval (str): Таймфрейм (для метаданных лога).
        strategy_name (str): Имя стратегии.
        risk_manager_name (str): Имя риск-менеджера.
        risk_manager_params (Dict): Параметры риск-менеджера.
    """

    def __init__(self,
                 trade_log_file: Optional[str],
                 exchange: str,
                 interval: str,
                 strategy_name: str,
                 risk_manager_name: str,
                 risk_manager_params: Dict[str, Any]):
        """
        Инициализирует процессор с метаданными для логирования.
        """
        self.trade_log_file = trade_log_file
        self.exchange = exchange
        self.interval = interval
        self.strategy_name = strategy_name
        self.risk_manager_name = risk_manager_name
        self.risk_manager_params = risk_manager_params

    def process_fill(self, event: FillEvent, state: PortfolioState):
        """
        Обрабатывает событие исполнения ордера.
        """
        instrument = event.instrument

        # Снимаем флаг ожидания ордера
        if instrument in state.pending_orders:
            state.pending_orders.remove(instrument)

        position = state.positions.get(instrument)

        if not position:
            self._handle_fill_open(event, state)
        else:
            self._handle_fill_close(event, state, position)

    def _format_price(self, price: float) -> str:
        """
        Умное форматирование цены для логов.
        Если цена очень маленькая (BabyDoge), показывает больше знаков.
        """
        if price < 0.0001:
            return f"{price:.12f}"
        elif price < 1.0:
            return f"{price:.6f}"
        else:
            return f"{price:.2f}"

    def _handle_fill_open(self, event: FillEvent, state: PortfolioState):
        """
        Регистрирует открытие новой позиции.
        """
        # 1. Рассчитываем полную стоимость входа (в валюте баланса, напр. USDT)
        raw_cost = (event.price * event.quantity) + event.commission
        entry_cost = round(raw_cost, PRECISION)

        # 2. Вычитаем из баланса
        state.current_capital = round(state.current_capital - entry_cost, PRECISION)

        new_position = Position(
            instrument=event.instrument,
            quantity=event.quantity,
            entry_price=event.price,
            entry_timestamp=event.timestamp,
            direction=event.direction,
            stop_loss=event.stop_loss,
            take_profit=event.take_profit,
            entry_commission=event.commission
        )

        state.positions[event.instrument] = new_position

        price_str = self._format_price(event.price)
        logger.info(
            f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {event.instrument} "
            f"@ {price_str}. Cost: {entry_cost:.2f}. New Balance: {state.current_capital:.2f}"
        )

    def _handle_fill_close(self, event: FillEvent, state: PortfolioState, position: Position):
        """
        Регистрирует закрытие позиции и фиксирует финансовый результат.
        """
        gross_pnl = 0.0

        if position.direction == TradeDirection.BUY:
            gross_pnl = (event.price - position.entry_price) * event.quantity
        else:  # TradeDirection.SELL
            gross_pnl = (position.entry_price - event.price) * event.quantity

        gross_pnl = round(gross_pnl, PRECISION)

        commission_exit = event.commission
        commission_entry = position.entry_commission

        # Чистая прибыль (Net PnL)
        pnl = round(gross_pnl - commission_entry - commission_exit, PRECISION)

        # 3. Рассчитываем выручку (Proceeds) для возврата на баланс.
        raw_proceeds = (position.entry_price * position.quantity) + gross_pnl - commission_exit
        proceeds = round(raw_proceeds, PRECISION)

        # Обновляем капитал
        state.current_capital = round(state.current_capital + proceeds, PRECISION)

        # Сохраняем статистику
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
            f"Позиция ЗАКРЫТА ({event.trigger_reason}): {event.instrument}. "
            f"PnL: {pnl:.2f}. Proceeds: {proceeds:.2f}. "
            f"Equity: {state.current_capital:.2f}"
        )