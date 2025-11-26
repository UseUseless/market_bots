import logging
from queue import Queue
from datetime import datetime

from app.shared.events import MarketEvent, OrderEvent
from app.core.portfolio.state import PortfolioState
from app.shared.primitives import TradeDirection, TriggerReason, Position

logger = logging.getLogger(__name__)

class RiskMonitor:
    """
    Сервис, отвечающий исключительно за мониторинг рисков по открытым позициям.

    На каждую новую свечу (MarketEvent) он проверяет, не достигли ли цены
    уровней Stop Loss или Take Profit для каждой активной позиции.
    """

    def __init__(self, events_queue: Queue):
        """
        Инициализирует монитор риска.

        :param events_queue: Ссылка на общую очередь событий для отправки ордеров на закрытие.
        """
        self.events_queue = events_queue

    def check_positions(self, market_event: MarketEvent, portfolio_state: PortfolioState):
        """
        Главный метод. Проверяет все открытые позиции на предмет срабатывания SL/TP.

        :param market_event: Событие с данными последней свечи.
        :param portfolio_state: Текущее состояние портфеля.
        """
        instrument = market_event.instrument
        position = portfolio_state.positions.get(instrument)

        # Проверяем только если по данному инструменту есть открытая позиция
        # и нет ожидающего исполнения ордера (чтобы не отправлять дублирующие ордера на закрытие)
        if not position or instrument in portfolio_state.pending_orders:
            return

        self._check_single_position(market_event, position)

    def _check_single_position(self, event: MarketEvent, position: Position):
        """
        Проверяет одну конкретную позицию на срабатывание SL или TP.
        Используется "правило первого стоп-лосса": проверка SL имеет приоритет.
        """
        candle_high = event.data['high']
        candle_low = event.data['low']

        # --- Проверка для ДЛИННОЙ позиции (BUY) ---
        if position.direction == TradeDirection.BUY:
            # Приоритетная проверка Stop Loss
            if candle_low <= position.stop_loss:
                logger.info(f"!!! СРАБОТАЛ STOP LOSS для {position.instrument} по цене {position.stop_loss:.4f}. Генерирую ордер на закрытие.")
                self._generate_exit_order(event.timestamp, position, TriggerReason.STOP_LOSS, position.stop_loss)
                return  # Выходим, чтобы не проверять TP на этой же свече

            # Проверка Take Profit (только если SL не сработал)
            if candle_high >= position.take_profit:
                logger.info(f"!!! СРАБОТАЛ TAKE PROFIT для {position.instrument} по цене {position.take_profit:.4f}. Генерирую ордер на закрытие.")
                self._generate_exit_order(event.timestamp, position, TriggerReason.TAKE_PROFIT, position.take_profit)
                return

        # --- Проверка для КОРОТКОЙ позиции (SELL) ---
        elif position.direction == TradeDirection.SELL:
            # Приоритетная проверка Stop Loss
            if candle_high >= position.stop_loss:
                logger.info(f"!!! СРАБОТАЛ STOP LOSS для {position.instrument} по цене {position.stop_loss:.4f}. Генерирую ордер на закрытие.")
                self._generate_exit_order(event.timestamp, position, TriggerReason.STOP_LOSS, position.stop_loss)
                return

            # Проверка Take Profit (только если SL не сработал)
            if candle_low <= position.take_profit:
                logger.info(f"!!! СРАБОТАЛ TAKE PROFIT для {position.instrument} по цене {position.take_profit:.4f}. Генерирую ордер на закрытие.")
                self._generate_exit_order(event.timestamp, position, TriggerReason.TAKE_PROFIT, position.take_profit)
                return

    def _generate_exit_order(self, timestamp: datetime, position: Position, reason: TriggerReason, execution_price: float):
        """
        Создает и отправляет в очередь событие OrderEvent на закрытие позиции.
        """
        exit_direction = TradeDirection.SELL if position.direction == TradeDirection.BUY else TradeDirection.BUY
        order = OrderEvent(
            timestamp=timestamp,
            instrument=position.instrument,
            quantity=position.quantity,
            direction=exit_direction,
            trigger_reason=reason,
            price_hint=execution_price
        )
        self.events_queue.put(order)
