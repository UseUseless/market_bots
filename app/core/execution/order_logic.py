"""
Модуль логики создания ордеров (Order Management).

Этот модуль является "мозговым центром" исполнения. Он превращает абстрактное желание
стратегии ("Хочу купить") в конкретный, безопасный и валидный ордер ("Купить 150 лотов").

Он объединяет компоненты:
1.  **RiskManager**: Определяет уровни SL/TP и денежный риск.
2.  **PositionSizer**: Переводит деньги в количество актива.
3.  **RulesValidator**: Подгоняет количество под шаг лота биржи.
"""

import logging
from queue import Queue
from typing import Dict, Any

import pandas as pd

from app.shared.events import SignalEvent, OrderEvent
from app.core.portfolio.state import PortfolioState
from app.core.risk.manager import BaseRiskManager
from app.core.risk.sizer import BasePositionSizer
from app.core.execution.rules import InstrumentRulesValidator
from app.shared.primitives import TradeDirection, TriggerReason
from app.shared.config import config

BACKTEST_CONFIG = config.BACKTEST_CONFIG

logger = logging.getLogger('backtester')


class OrderManager:
    """
    Сервис управления жизненным циклом создания ордера.

    Отвечает за валидацию сигналов, расчет объема позиции с учетом всех ограничений
    (риск, капитал, правила биржи) и генерацию события `OrderEvent`.

    Attributes:
        events_queue (Queue): Очередь для отправки готовых ордеров.
        risk_manager (BaseRiskManager): Сервис расчета рисков.
        position_sizer (BasePositionSizer): Сервис расчета объема.
        rules_validator (InstrumentRulesValidator): Сервис проверки правил инструмента.
        max_exposure (float): Максимальная доля капитала на одну позицию (0.0 - 1.0).
    """

    def __init__(self,
                 events_queue: Queue,
                 risk_manager: BaseRiskManager,
                 position_sizer: BasePositionSizer,
                 instrument_info: Dict[str, Any]):
        """
        Инициализирует менеджер ордеров.

        Args:
            events_queue (Queue): Системная шина событий.
            risk_manager (BaseRiskManager): Реализация риск-менеджера.
            position_sizer (BasePositionSizer): Реализация сайзера.
            instrument_info (Dict[str, Any]): Метаданные инструмента (лотность, шаги).
        """
        self.events_queue = events_queue
        self.risk_manager = risk_manager
        self.position_sizer = position_sizer
        self.rules_validator = InstrumentRulesValidator(instrument_info)
        self.max_exposure = BACKTEST_CONFIG.get("MAX_POSITION_EXPOSURE", 0.9)

    def process_signal(self, event: SignalEvent, state: PortfolioState, last_candle: pd.Series):
        """
        Маршрутизирует сигнал (вход или выход) в соответствующий обработчик.

        Args:
            event (SignalEvent): Входящий сигнал от стратегии.
            state (PortfolioState): Текущее состояние портфеля.
            last_candle (pd.Series): Данные последней свечи (для цен и ATR).
        """
        instrument = event.instrument
        position = state.positions.get(instrument)

        # Idempotency Check:
        # Если по инструменту уже отправлен ордер, но еще не пришло подтверждение (Fill),
        # мы игнорируем новые сигналы, чтобы не открыть позицию дважды или не перевернуться случайно.
        if instrument in state.pending_orders:
            logger.warning(f"Сигнал по {instrument} проигнорирован: есть активный pending order.")
            return

        # Маршрутизация
        if not position:
            # Нет позиции -> Пытаемся открыть (Entry)
            self._handle_entry_signal(event, state, last_candle)
        else:
            # Есть позиция -> Пытаемся закрыть или перевернуться (Exit)
            # Примечание: Переворот (Reverse) пока не реализован, только закрытие.
            self._handle_exit_signal(event, state)

    def _handle_entry_signal(self, event: SignalEvent, state: PortfolioState, last_candle: pd.Series):
        """
        Обрабатывает сигнал на вход в позицию.

        Реализует "Воронку сайзинга" (Sizing Funnel):
        1.  **Risk Calc**: Считаем объем исходя из допустимого убытка (SL).
        2.  **Exposure Calc**: Считаем объем исходя из макс. доли в портфеле.
        3.  **Min**: Берем меньшее из двух (консервативный подход).
        4.  **Rounding**: Округляем до лота биржи.
        5.  **Cap Check**: Проверяем, хватает ли кэша.

        Args:
            event (SignalEvent): Сигнал.
            state (PortfolioState): Состояние портфеля.
            last_candle (pd.Series): Свечные данные.
        """
        # Используем Close свечи как ориентир цены входа.
        # В реальности цена будет отличаться (проскальзывание), но для расчета рисков это лучшая оценка.
        ideal_entry_price = last_candle['close']

        if ideal_entry_price <= 0:
            logger.warning(f"Некорректная цена входа ({ideal_entry_price}) для {event.instrument}. Пропуск.")
            return

        try:
            # 1. Расчет через Риск (Stop Loss)
            # Используем ОБЩИЙ капитал (Equity), а не свободный кэш, для расчета % риска.
            risk_profile = self.risk_manager.calculate_risk_profile(
                entry_price=ideal_entry_price,
                direction=event.direction,
                capital=state.current_capital,
                last_candle=last_candle
            )
            quantity_from_risk = self.position_sizer.calculate_size(risk_profile)

            # 2. Расчет через Концентрацию (Max Exposure)
            # Не позволяет одной позиции занимать больше X% портфеля (даже если стоп короткий).
            max_investment_amount = state.current_capital * self.max_exposure
            quantity_from_exposure = max_investment_amount / ideal_entry_price

            # 3. Выбор лимитирующего фактора
            final_quantity_ideal = min(quantity_from_risk, quantity_from_exposure)

            # 4. Корректировка под правила биржи (округление)
            final_quantity = self.rules_validator.adjust_quantity(final_quantity_ideal)

            # 5. Финальные проверки и создание ордера
            if final_quantity > 0:
                limiting_factor = "Risk" if quantity_from_risk < quantity_from_exposure else "Exposure"

                # Расчет стоимости (для спота без плеча)
                order_cost = final_quantity * ideal_entry_price

                # Проверка Buying Power (хватает ли свободных денег)
                if order_cost > state.available_capital:
                    logger.warning(
                        f"Недостаточно средств для {event.instrument}. "
                        f"Нужно: {order_cost:.2f}, Есть: {state.available_capital:.2f}. Отмена."
                    )
                    return

                logger.info(
                    f"Sizing ({limiting_factor}): RiskQ={quantity_from_risk:.4f}, ExpQ={quantity_from_exposure:.4f} "
                    f"-> Adjusted: {final_quantity}"
                )

                # Создание ордера
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

                # Блокируем инструмент от повторных сигналов до исполнения
                state.pending_orders.add(event.instrument)

                logger.info(f"ORDER SENT: {event.direction} {final_quantity} {event.instrument}")

            else:
                logger.info(f"Расчетный объем ({final_quantity}) слишком мал (меньше min_qty). Пропуск.")

        except ValueError as e:
            logger.warning(f"Ошибка расчета риска для {event.instrument}: {e}. Пропуск.")

    def _handle_exit_signal(self, event: SignalEvent, state: PortfolioState):
        """
        Обрабатывает сигнал на выход из позиции.

        Проверяет, совпадает ли направление сигнала с направлением закрытия
        (например, если позиция BUY, сигнал должен быть SELL).

        Args:
            event (SignalEvent): Сигнал выхода.
            state (PortfolioState): Состояние портфеля.
        """
        position = state.positions.get(event.instrument)

        # Проверка логики: Сигнал на выход должен быть противоположен позиции
        is_exit_signal = (
                (event.direction == TradeDirection.SELL and position.direction == TradeDirection.BUY) or
                (event.direction == TradeDirection.BUY and position.direction == TradeDirection.SELL)
        )

        if is_exit_signal:
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                quantity=position.quantity,  # Всегда закрываем позицию полностью
                direction=event.direction,
                trigger_reason=TriggerReason.SIGNAL
            )
            self.events_queue.put(order)
            state.pending_orders.add(event.instrument)
            logger.info(f"ORDER SENT (CLOSE): {event.direction} {position.quantity} {event.instrument}")