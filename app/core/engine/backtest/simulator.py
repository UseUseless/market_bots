"""
Симулятор исполнения ордеров (Execution Simulator).

Этот модуль эмулирует работу биржевого движка (Matching Engine) в режиме бэктеста.
Его задача — превратить ордер (намерение) в сделку (факт) с учетом рыночных условий.

Основные функции:
    - Расчет цены исполнения (Market vs Limit).
    - Симуляция проскальзывания (Slippage) на основе объема свечи.
    - Расчет торговых комиссий.
    - Проброс параметров риска (SL/TP) в событие исполнения.
"""

from queue import Queue
from typing import Dict, Any

import pandas as pd

from app.shared.events import OrderEvent, FillEvent
from app.shared.primitives import TradeDirection


class BacktestExecutionHandler:
    """
    Обработчик исполнения ордеров для симуляции.

    Принимает OrderEvent, рассчитывает итоговую цену и комиссию,
    генерирует FillEvent и помещает его в очередь событий.
    """

    def __init__(self, events_queue: Queue, commission_rate: float, slippage_config: Dict[str, Any]):
        """
        Инициализирует симулятор.

        Args:
            events_queue: Очередь для отправки событий исполнения (FillEvent).
            commission_rate: Размер комиссии (в долях, например 0.001 для 0.1%).
            slippage_config: Настройки проскальзывания (ENABLED, IMPACT_COEFFICIENT).
        """
        self.events_queue = events_queue
        self.commission_rate = commission_rate
        self.slippage_enabled = slippage_config.get("ENABLED", False)
        self.impact_coefficient = slippage_config.get("IMPACT_COEFFICIENT", 0.1)

    def _simulate_slippage(self, ideal_price: float, quantity: float,
                           direction: TradeDirection, candle_volume: float) -> float:
        """
        Рассчитывает цену с учетом влияния объема на рынок (Market Impact).

        Использует модель "Square Root Law": чем больше объем заявки относительно
        объема свечи, тем хуже цена исполнения.

        Args:
            ideal_price: Базовая цена исполнения.
            quantity: Объем ордера.
            direction: Направление сделки.
            candle_volume: Объем торгов в текущей свече.

        Returns:
            float: Скорректированная цена исполнения.
        """
        if not self.slippage_enabled or candle_volume <= 0:
            return ideal_price

        # Доля ордера в объеме свечи (ограничена 100%)
        volume_ratio = min(quantity / candle_volume, 1.0)

        # Расчет процента сдвига цены
        slippage_percent = self.impact_coefficient * (volume_ratio ** 0.5)

        # Жесткое ограничение проскальзывания (макс 20%), чтобы избежать аномалий
        slippage_percent = min(slippage_percent, 0.20)

        # Покупка исполняется дороже, Продажа — дешевле
        if direction == TradeDirection.BUY:
            return ideal_price * (1 + slippage_percent)
        else:
            return ideal_price * (1 - slippage_percent)

    def execute_order(self, order: OrderEvent, last_candle: pd.Series):
        """
        Исполняет ордер по текущим рыночным данным.

        Алгоритм:
        1. Определяет базовую цену (Limit Price из ордера или Open свечи для Market).
        2. Применяет проскальзывание.
        3. Считает комиссию.
        4. Создает FillEvent и копирует в него параметры SL/TP для портфеля.

        Args:
            order: Событие ордера.
            last_candle: Данные свечи, на которой происходит исполнение.
        """
        if last_candle is None:
            return

        # 1. Определение цены
        if order.price is not None:
            # Лимитный/Стоп ордер: исполняем по заданной цене
            base_price = order.price
        else:
            # Рыночный ордер: исполняем по цене открытия свечи
            base_price = last_candle['open']

        # 2. Проскальзывание
        exec_price = self._simulate_slippage(
            ideal_price=base_price,
            quantity=order.quantity,
            direction=order.direction,
            candle_volume=last_candle.get('volume', 1000000)
        )

        # 3. Комиссия
        commission = exec_price * order.quantity * self.commission_rate

        # 4. Генерация события
        fill = FillEvent(
            timestamp=order.timestamp,
            instrument=order.instrument,
            direction=order.direction,
            quantity=order.quantity,
            price=exec_price,
            commission=commission,
            trigger_reason=order.trigger_reason,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit
        )

        # Динамически прикрепляем уровни SL/TP к событию исполнения,
        # чтобы Portfolio мог сохранить их в объекте Trade.
        # my_question а нафига так сделано?
        fill.stop_loss = order.stop_loss
        fill.take_profit = order.take_profit

        self.events_queue.put(fill)