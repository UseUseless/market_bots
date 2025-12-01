"""
Модуль мониторинга рисков (Risk Monitor).

Отвечает за "пассивную" защиту позиций. В отличие от стратегии, которая ищет входы,
этот компонент следит за тем, чтобы цена не вышла за допустимые границы (SL/TP).
Работает на каждом тике (или обновлении свечи).
"""

import logging
from queue import Queue
from datetime import datetime

from app.shared.events import MarketEvent, OrderEvent
from app.core.portfolio.state import PortfolioState
from app.shared.primitives import TradeDirection, TriggerReason, Position

logger = logging.getLogger(__name__)


class RiskMonitor:
    """
    Сервис автоматического контроля открытых позиций.

    При поступлении новых рыночных данных проверяет, не пересекла ли цена
    уровни Stop Loss или Take Profit. Если пересекла — генерирует ордер на закрытие
    и НЕМЕДЛЕННО блокирует инструмент флагом pending_orders.

    Attributes:
        events_queue (Queue): Очередь событий для отправки `OrderEvent`.
    """

    def __init__(self, events_queue: Queue):
        """
        Инициализирует монитор.

        Args:
            events_queue (Queue): Ссылка на системную шину/очередь событий.
        """
        self.events_queue = events_queue

    def check_positions(self, market_event: MarketEvent, portfolio_state: PortfolioState):
        """
        Проверяет все открытые позиции по текущему инструменту.

        Вызывается при получении `MarketEvent`.

        Args:
            market_event (MarketEvent): Событие с данными новой свечи.
            portfolio_state (PortfolioState): Текущее состояние портфеля с позициями.
        """
        instrument = market_event.instrument
        position = portfolio_state.positions.get(instrument)

        # 1. Если позиции нет — нечего проверять.
        # 2. Если по инструменту уже висит активный ордер (в pending_orders),
        #    значит мы уже в процессе выхода или входа. Не вмешиваемся.
        if not position or instrument in portfolio_state.pending_orders:
            return

        self._check_single_position(market_event, position, portfolio_state)

    def _check_single_position(self, event: MarketEvent, position: Position, state: PortfolioState):
        """
        Проверяет условия выхода для конкретной позиции.

        Использует цены High и Low текущей свечи, чтобы определить, было ли
        касание уровня внутри периода.

        Приоритет проверок (Pessimistic approach):
        Сначала проверяется Stop Loss. Если в одной свече были задеты и SL, и TP,
        считается, что сработал SL. Это защищает от завышения результатов в бэктестах.

        Args:
            event (MarketEvent): Рыночные данные.
            position (Position): Позиция для проверки.
            state (PortfolioState): Состояние портфеля для блокировки инструмента.
        """
        candle_high = event.data['high']
        candle_low = event.data['low']

        # --- Логика для LONG (Покупка) ---
        if position.direction == TradeDirection.BUY:
            # 1. Проверка Stop Loss (Цена упала ниже уровня)
            if candle_low <= position.stop_loss:
                logger.info(f"!!! СРАБОТАЛ STOP LOSS для {position.instrument} "
                            f"@{position.stop_loss:.4f} (Low: {candle_low}).")
                self._generate_exit_order(
                    event.timestamp, position, TriggerReason.STOP_LOSS, position.stop_loss, state
                )
                return  # Важно: прерываем выполнение, чтобы не сработал TP

            # 2. Проверка Take Profit (Цена выросла выше уровня)
            if candle_high >= position.take_profit:
                logger.info(f"!!! СРАБОТАЛ TAKE PROFIT для {position.instrument} "
                            f"@{position.take_profit:.4f} (High: {candle_high}).")
                self._generate_exit_order(
                    event.timestamp, position, TriggerReason.TAKE_PROFIT, position.take_profit, state
                )
                return

        # --- Логика для SHORT (Продажа) ---
        elif position.direction == TradeDirection.SELL:
            # 1. Проверка Stop Loss (Цена выросла выше уровня)
            if candle_high >= position.stop_loss:
                logger.info(f"!!! СРАБОТАЛ STOP LOSS для {position.instrument} "
                            f"@{position.stop_loss:.4f} (High: {candle_high}).")
                self._generate_exit_order(
                    event.timestamp, position, TriggerReason.STOP_LOSS, position.stop_loss, state
                )
                return

            # 2. Проверка Take Profit (Цена упала ниже уровня)
            if candle_low <= position.take_profit:
                logger.info(f"!!! СРАБОТАЛ TAKE PROFIT для {position.instrument} "
                            f"@{position.take_profit:.4f} (Low: {candle_low}).")
                self._generate_exit_order(
                    event.timestamp, position, TriggerReason.TAKE_PROFIT, position.take_profit, state
                )
                return

    def _generate_exit_order(self, timestamp: datetime, position: Position, reason: TriggerReason,
                             execution_price: float, state: PortfolioState):
        """
        Создает ордер на закрытие позиции.

        Args:
            timestamp (datetime): Время генерации сигнала.
            position (Position): Позиция, которую нужно закрыть.
            reason (TriggerReason): Причина (SL или TP).
            execution_price (float): Цена, по которой должен исполниться ордер
                (уровень SL или TP). Передается как `price_hint` для симулятора.
            state (PortfolioState): Состояние портфеля.
        """
        # Закрытие = сделка в противоположном направлении
        exit_direction = TradeDirection.SELL if position.direction == TradeDirection.BUY else TradeDirection.BUY

        order = OrderEvent(
            timestamp=timestamp,
            instrument=position.instrument,
            quantity=position.quantity,  # Закрываем полный объем
            direction=exit_direction,
            trigger_reason=reason,
            price_hint=execution_price  # Подсказка симулятору: "исполни по этой цене"
        )
        self.events_queue.put(order)

        # Добавляем инструмент в pending_orders.
        # Это предотвратит:
        # 1. Повторное срабатывание RiskMonitor на этом же тике/свече.
        # 2. Срабатывание стратегии (OrderManager), если она тоже захочет закрыть позицию.
        state.pending_orders.add(position.instrument)

        logger.debug(f"RiskMonitor: Sent {reason} order for {position.instrument}. Added to pending_orders.")