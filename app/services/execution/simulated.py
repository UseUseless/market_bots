from queue import Queue
import pandas as pd
from typing import Any, Dict

from app.core.models.event import OrderEvent, FillEvent
from app.services.execution.abc import BaseExecutionHandler
from app.core.constants import TradeDirection


class SimulatedExecutionHandler(BaseExecutionHandler):
    """
    Симулятор исполнения ордеров для бэктестинга.

    Теперь он не просто транслирует OrderEvent в FillEvent, а выполняет
    полноценную симуляцию:
    - Рассчитывает цену исполнения с учетом проскальзывания (slippage).
    - Рассчитывает комиссию за сделку.
    - Генерирует FillEvent с финальными, "реалистичными" данными.
    """

    def __init__(self,
                 events_queue: Queue,
                 commission_rate: float,
                 slippage_config: Dict[str, Any]):
        super().__init__(events_queue)
        self.commission_rate = commission_rate
        self.slippage_enabled = slippage_config.get("ENABLED", False)
        self.impact_coefficient = slippage_config.get("IMPACT_COEFFICIENT", 0.1)

    def _simulate_slippage(self, ideal_price: float, quantity: int, direction: TradeDirection, candle_volume: int) -> float:
        """
        Приватный метод для симуляции проскальзывания (slippage).
        Логика полностью перенесена из старого класса Portfolio.
        """
        if not self.slippage_enabled or candle_volume <= 0:
            return ideal_price

        volume_ratio = min(quantity / candle_volume, 1.0)
        slippage_percent = self.impact_coefficient * (volume_ratio ** 0.5)

        MAX_SLIPPAGE_PERCENT = 0.20  # 20%
        slippage_percent = min(slippage_percent, MAX_SLIPPAGE_PERCENT)

        if direction == TradeDirection.BUY:
            return ideal_price * (1 + slippage_percent)
        else:  # TradeDirection.SELL
            return ideal_price * (1 - slippage_percent)

    def execute_order(self, event: OrderEvent, last_candle: pd.Series):
        """
        Исполняет ордер, симулируя рыночные условия.
        """
        if last_candle is None:
            raise ValueError("Для симуляции исполнения необходимы данные последней свечи.")

        # 1. Определяем "идеальную" цену исполнения.
        #    - Если в OrderEvent есть price_hint (от SL/TP), используем его.
        #    - Иначе (ордер по сигналу), используем цену открытия следующей свечи.
        #   Это устранение Look-Ahead Bias.
        if event.price_hint is not None:
            ideal_price = event.price_hint
        else:
            ideal_price = last_candle['open']

        # 2. Рассчитываем РЕАЛЬНУЮ цену исполнения с учетом проскальзывания
        execution_price = self._simulate_slippage(
            ideal_price=ideal_price,
            quantity=event.quantity,
            direction=event.direction,
            candle_volume=last_candle['volume']
        )

        # 3. Рассчитываем комиссию
        # ВАЖНО: Комиссия за сделку (вход/выход) рассчитывается здесь.
        # Для простоты считаем ее от суммы сделки.
        # При закрытии позиции комиссия будет за полный оборот (вход+выход).
        # Эту логику должен будет обработать FillProcessor.
        # Здесь мы считаем комиссию только за ТЕКУЩУЮ операцию.
        commission = execution_price * event.quantity * self.commission_rate

        # 4. Создаем FillEvent с фактическими данными
        fill_event = FillEvent(
            timestamp=event.timestamp,
            instrument=event.instrument,
            quantity=event.quantity,
            direction=event.direction,
            price=execution_price,
            commission=commission,
            trigger_reason=event.trigger_reason,
            stop_loss=event.stop_loss,
            take_profit=event.take_profit
        )
        self.events_queue.put(fill_event)