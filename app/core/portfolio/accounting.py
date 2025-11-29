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


class FillProcessor:
    """
    Обработчик исполненных сделок (Accounting Engine).

    Выполняет две основные функции:
    1.  **Управление состоянием:** Создает объекты `Position` при входе и удаляет их при выходе.
    2.  **Финансовый учет:** Рассчитывает PnL (Profit and Loss), учитывает комиссии
        и обновляет баланс (`current_capital`) портфеля.

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

        Args:
            trade_log_file (Optional[str]): Путь к файлу логов. Если None, запись в файл отключена.
            exchange (str): Название биржи (metadata).
            interval (str): Рабочий интервал (metadata).
            strategy_name (str): Идентификатор стратегии (metadata).
            risk_manager_name (str): Идентификатор РМ (metadata).
            risk_manager_params (Dict[str, Any]): Параметры РМ (metadata).
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

        Маршрутизирует событие на открытие или закрытие позиции в зависимости
        от текущего состояния портфеля. Также снимает блокировку `pending_orders`.

        Args:
            event (FillEvent): Событие исполнения сделки от биржи/симулятора.
            state (PortfolioState): Текущее состояние портфеля для обновления.
        """
        instrument = event.instrument

        # Снимаем флаг ожидания ордера, так как ордер исполнен
        if instrument in state.pending_orders:
            state.pending_orders.remove(instrument)

        position = state.positions.get(instrument)

        if not position:
            # Если позиции нет, значит это вход (Entry)
            self._handle_fill_open(event, state)
        else:
            # Если позиция есть, значит это выход (Exit)
            # Примечание: Частичное закрытие или усреднение тут пока не реализовано,
            # предполагается полное закрытие.
            self._handle_fill_close(event, state, position)

    def _handle_fill_open(self, event: FillEvent, state: PortfolioState):
        """
        Регистрирует открытие новой позиции.

        Создает объект `Position` и сохраняет его в стейт.
        Комиссия за вход сохраняется внутри позиции, чтобы учесть её позже при расчете PnL.

        Args:
            event (FillEvent): Событие входа.
            state (PortfolioState): Состояние портфеля.
        """
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

        logger.info(
            f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {event.instrument} "
            f"@ {event.price:.4f} | SL: {new_position.stop_loss:.4f}, TP: {new_position.take_profit:.4f}"
        )

    def _handle_fill_close(self, event: FillEvent, state: PortfolioState, position: Position):
        """
        Регистрирует закрытие позиции и фиксирует финансовый результат.

        Алгоритм PnL (для линейного рынка/Spot):
        1.  Gross PnL: Разница цен * Количество.
        2.  Net PnL: Gross PnL - Комиссия входа - Комиссия выхода.
        3.  Update Capital: Капитал += Net PnL.

        Args:
            event (FillEvent): Событие выхода.
            state (PortfolioState): Состояние портфеля.
            position (Position): Объект позиции, которую закрываем.
        """
        gross_pnl = 0.0

        # Расчет "грязной" прибыли (без комиссий)
        if position.direction == TradeDirection.BUY:
            # Long: (Exit - Entry) * Qty
            gross_pnl = (event.price - position.entry_price) * event.quantity
        else:  # TradeDirection.SELL
            # Short: (Entry - Exit) * Qty
            gross_pnl = (position.entry_price - event.price) * event.quantity

        commission_exit = event.commission
        commission_entry = position.entry_commission

        # Чистая прибыль
        pnl = gross_pnl - commission_entry - commission_exit

        # Обновляем "живые" деньги в портфеле
        state.current_capital += pnl

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

        # Добавляем в историю сессии (для быстрого расчета метрик без чтения файла)
        state.closed_trades.append({
            'pnl': pnl,
            'entry_timestamp_utc': position.entry_timestamp,
            'exit_timestamp_utc': event.timestamp
        })

        # Удаляем позицию из активных
        del state.positions[event.instrument]

        logger.info(
            f"Позиция ЗАКРЫТА ({event.trigger_reason}): {event.instrument}. "
            f"PnL: {pnl:.2f} (Gross: {gross_pnl:.2f}, Comm: {commission_entry + commission_exit:.2f}). "
            f"Equity: {state.current_capital:.2f}"
        )