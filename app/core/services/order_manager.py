import logging
from queue import Queue
import pandas as pd
from typing import Dict, Any

from app.core.models.event import SignalEvent, OrderEvent
from app.core.models.portfolio_state import PortfolioState
from app.core.risk.risk_manager import BaseRiskManager
from app.core.risk.sizer import BasePositionSizer
from app.core.services.instrument_rules import InstrumentRulesValidator
from app.core.constants import TradeDirection, TriggerReason
from config import BACKTEST_CONFIG

logger = logging.getLogger('backtester')

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
        self.max_exposure = BACKTEST_CONFIG.get("MAX_POSITION_EXPOSURE", 0.9)

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
        """
        Обрабатывает сигнал на открытие новой позиции с двухступенчатым контролем размера.
        """
        ideal_entry_price = last_candle['close']

        if ideal_entry_price <= 0:
            logger.warning(f"Идеальная цена входа равна нулю или отрицательна для {event.instrument}. Сигнал проигнорирован.")
            return

        try:
            # --- Шаг 1: Расчет размера позиции на основе РИСКА НА СДЕЛКУ ---
            # Используем ОБЩИЙ текущий капитал для расчета допустимого убытка.
            risk_profile = self.risk_manager.calculate_risk_profile(
                entry_price=ideal_entry_price,
                direction=event.direction,
                capital=state.current_capital,
                last_candle=last_candle
            )
            quantity_from_risk = self.position_sizer.calculate_size(risk_profile)

            # --- Шаг 2: Расчет размера позиции на основе ЛИМИТА КОНЦЕНТРАЦИИ ---
            # Используем ОБЩИЙ текущий капитал для расчета максимального размера вложения.
            max_investment_amount = state.current_capital * self.max_exposure
            quantity_from_exposure = max_investment_amount / ideal_entry_price

            # --- Шаг 3: Выбор наиболее консервативного (наименьшего) размера ---
            final_quantity_ideal = min(quantity_from_risk, quantity_from_exposure)

            # --- Шаг 4: Корректировка по правилам биржи (лотность, шаг) ---
            final_quantity = self.rules_validator.adjust_quantity(final_quantity_ideal)

            # --- Шаг 5: Финальная проверка и создание ордера ---
            if final_quantity > 0:
                # Определяем, какой из лимитов сработал, для логирования
                limiting_factor = "Риск" if quantity_from_risk < quantity_from_exposure else "Концентрация"
                logger.info(
                    f"Расчет размера позиции ({limiting_factor} лимит): "
                    f"Q(риск): {quantity_from_risk:.2f}, Q(конц): {quantity_from_exposure:.2f} -> "
                    f"Выбрано: {final_quantity_ideal:.2f} -> Скорректировано: {final_quantity}"
                )

                order_cost = final_quantity * ideal_entry_price

                # Финальный предохранитель: проверяем, хватает ли СВОБОДНЫХ средств.
                if order_cost > state.available_capital:
                    logger.warning(
                        f"Недостаточно СВОБОДНОГО капитала для открытия позиции по {event.instrument}. "
                        f"Требуется: {order_cost:.2f}, доступно: {state.available_capital:.2f}. "
                        f"Сигнал проигнорирован."
                    )
                    return

                order = OrderEvent(
                    timestamp=event.timestamp,
                    instrument=event.instrument,
                    quantity=final_quantity,
                    direction=event.direction,
                    trigger_reason=TriggerReason.SIGNAL,
                    stop_loss=risk_profile.stop_loss_price,
                    take_profit=risk_profile.take_profit_price
                )
                self.events_queue.put(order)
                state.pending_orders.add(event.instrument)
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
        is_exit_signal = (
                (event.direction == TradeDirection.SELL and position.direction == TradeDirection.BUY) or
                (event.direction == TradeDirection.BUY and position.direction == TradeDirection.SELL)
        )

        if is_exit_signal:
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                quantity=position.quantity,  # Закрываем всю позицию
                direction=event.direction,
                trigger_reason=TriggerReason.SIGNAL
            )
            self.events_queue.put(order)
            state.pending_orders.add(event.instrument)
            logger.info(f"OrderManager генерирует ордер на ЗАКРЫТИЕ позиции по {event.instrument}")