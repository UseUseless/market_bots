import logging
from queue import Queue
import pandas as pd
from typing import Dict, Any

from app.core.models.event import SignalEvent, OrderEvent
from app.core.models.portfolio_state import PortfolioState
from app.core.risk.risk_manager import BaseRiskManager
from app.core.risk.sizer import BasePositionSizer
from app.core.services.instrument_rules import InstrumentRulesValidator

logger = logging.getLogger(__name__)

class OrderManager:
    """
    Сервис, отвечающий за преобразование торговых сигналов (SignalEvent)
    в конкретные ордера (OrderEvent).

    Он является центральной точкой, где объединяется логика управления
    рисками (RiskManager), расчета размера позиции (PositionSizer) и
    учета правил инструмента (InstrumentRulesValidator).
    """

    def __init__(self,
                 events_queue: Queue,
                 risk_manager: BaseRiskManager,
                 position_sizer: BasePositionSizer,
                 instrument_info: Dict[str, Any]):
        self.events_queue = events_queue
        self.risk_manager = risk_manager
        self.position_sizer = position_sizer
        self.rules_validator = InstrumentRulesValidator(instrument_info)

    def process_signal(self, event: SignalEvent, state: PortfolioState, last_candle: pd.Series):
        """
        Обрабатывает сигнал от стратегии.
        """
        instrument = event.instrument
        position = state.positions.get(instrument)

        # Фильтр: Игнорируем сигналы, если ордер по инструменту уже в обработке.
        if instrument in state.pending_orders:
            logger.warning(f"Сигнал по {instrument} проигнорирован, т.к. есть ожидающий ордер.")
            return

        # --- Сценарий 1: У нас НЕТ открытой позиции по этому инструменту ---
        if not position:
            self._handle_entry_signal(event, state, last_candle)
        # --- Сценарий 2: У нас ЕСТЬ открытая позиция по этому инструменту ---
        else:
            self._handle_exit_signal(event, state)

    def _handle_entry_signal(self, event: SignalEvent, state: PortfolioState, last_candle: pd.Series):
        """Обрабатывает сигнал на открытие новой позиции."""
        ideal_entry_price = last_candle['open']

        try:
            # 1. Рассчитываем профиль риска (SL, TP, риск на акцию)
            risk_profile = self.risk_manager.calculate_risk_profile(
                entry_price=ideal_entry_price,
                direction=event.direction,
                capital=state.current_capital,
                last_candle=last_candle
            )

            # 2. Рассчитываем "идеальное" количество на основе профиля риска
            quantity_float = self.position_sizer.calculate_size(risk_profile)

            # 3. Корректируем количество с учетом правил биржи (лотность, шаг и т.д.)
            final_quantity = self.rules_validator.adjust_quantity(quantity_float)

            # 4. Если расчетное количество больше нуля, генерируем ордер.
            if final_quantity > 0:
                logger.info(
                    f"Расчетное кол-во: {quantity_float:.4f}, скорректировано до {final_quantity} "
                    f"с учетом правил биржи."
                )
                order = OrderEvent(
                    timestamp=event.timestamp,
                    instrument=event.instrument,
                    quantity=final_quantity,
                    direction=event.direction,
                    trigger_reason="SIGNAL"
                )
                self.events_queue.put(order)
                state.pending_orders.add(event.instrument)  # Сразу помечаем ордер как ожидающий
                logger.info(f"OrderManager генерирует ордер на {event.direction} {final_quantity} "
                            f"лот(ов) {event.instrument}")
            else:
                logger.info(
                    f"Расчетное кол-во ({final_quantity}) слишком мало для создания ордера. Сигнал проигнорирован.")

        except ValueError as e:
            logger.warning(f"Не удалось рассчитать профиль риска для {event.instrument}: {e}. Сигнал проигнорирован.")

    def _handle_exit_signal(self, event: SignalEvent, state: PortfolioState):
        """Обрабатывает сигнал на закрытие существующей позиции."""
        position = state.positions.get(event.instrument)

        # Сигнал на закрытие - это сигнал в противоположном направлении
        is_exit_signal = (event.direction == "SELL" and position.direction == 'BUY') or \
                         (event.direction == "BUY" and position.direction == 'SELL')

        if is_exit_signal:
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                quantity=position.quantity,  # Закрываем всю позицию
                direction=event.direction,
                trigger_reason="SIGNAL"
            )
            self.events_queue.put(order)
            state.pending_orders.add(event.instrument)
            logger.info(f"OrderManager генерирует ордер на ЗАКРЫТИЕ позиции по {event.instrument}")