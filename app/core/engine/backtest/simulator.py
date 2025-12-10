"""
Модуль симуляции исполнения ордеров (Simulated Execution).

Используется в бэктестах для эмуляции поведения биржи.
В отличие от "наивных" тестеров, этот модуль учитывает:
1.  **Проскальзывание (Slippage):** Цена ухудшается при больших объемах или низкой ликвидности.
2.  **Комиссии:** Расчет затрат на сделку.
3.  **Реалистичные цены входа:** Исполнение по Open следующей свечи для сигналов.
"""

from queue import Queue
import pandas as pd
from typing import Any, Dict

from app.shared.events import OrderEvent, FillEvent
from app.shared.primitives import TradeDirection


class BacktestExecutionHandler:
    """
    Симулятор биржевого исполнения для бэктеста.

    Превращает `OrderEvent` в `FillEvent` мгновенно (без сетевых задержек),
    но с корректировкой цены и расчетом комиссий.

    Attributes:
        commission_rate (float): Ставка комиссии (например, 0.0005 для 0.05%).
        slippage_enabled (bool): Включена ли симуляция проскальзывания.
        impact_coefficient (float): Коэффициент чувствительности цены к объему.
                                    Чем выше, тем сильнее цена уходит против нас при большом объеме.
    """

    def __init__(self,
                 events_queue: Queue,
                 commission_rate: float,
                 slippage_config: Dict[str, Any]):
        """
        Инициализирует симулятор.

        Args:
            events_queue (Queue): Очередь для отправки событий исполнения (Fill).
            commission_rate (float): Размер комиссии (в долях единицы).
            slippage_config (Dict[str, Any]): Настройки проскальзывания.
                                              Пример: {"ENABLED": True, "IMPACT_COEFFICIENT": 0.1}.
        """
        super().__init__(events_queue)
        self.commission_rate = commission_rate
        self.slippage_enabled = slippage_config.get("ENABLED", False)
        self.impact_coefficient = slippage_config.get("IMPACT_COEFFICIENT", 0.1)

    def _simulate_slippage(self, ideal_price: float, quantity: int,
                           direction: TradeDirection, candle_volume: int) -> float:
        """
        Рассчитывает цену исполнения с учетом влияния объема ордера на стакан.

        Модель: "Square Root Law of Market Impact".
        `Slippage % = Impact_Coeff * sqrt(Order_Qty / Candle_Volume)`

        Args:
            ideal_price (float): "Чистая" цена (например, Open свечи или уровень SL).
            quantity (int): Объем ордера.
            direction (TradeDirection): Направление сделки.
            candle_volume (int): Общий объем торгов в этой свече (ликвидность).

        Returns:
            float: Ухудшенная цена исполнения.
        """
        if not self.slippage_enabled or candle_volume <= 0:
            return ideal_price

        # Доля нашего ордера в объеме свечи
        volume_ratio = min(quantity / candle_volume, 1.0)

        # Расчет процента проскальзывания
        slippage_percent = self.impact_coefficient * (volume_ratio ** 0.5)

        # Hard cap: проскальзывание не может быть больше 20% (защита от аномалий в данных)
        MAX_SLIPPAGE_PERCENT = 0.20
        slippage_percent = min(slippage_percent, MAX_SLIPPAGE_PERCENT)

        # Ухудшаем цену: Покупка дороже, Продажа дешевле
        if direction == TradeDirection.BUY:
            return ideal_price * (1 + slippage_percent)
        else:  # TradeDirection.SELL
            return ideal_price * (1 - slippage_percent)

    def execute_order(self, event: OrderEvent, last_candle: pd.Series):
        """
        Исполняет ордер.

        Логика выбора цены:
        1. Если это StopLoss/TakeProfit (`price` задан): Исполняем по уровню стопа
           (с добавлением проскальзывания).
        2. Если это Рыночный вход по сигналу (`price` is None): Исполняем по цене
           OPEN текущей свечи (`last_candle['open']`).
           *Почему Open?* Потому что сигнал генерируется по Close предыдущей свечи (`t-1`).
           Физически мы можем войти только на открытии следующей (`t`).

        Args:
            event (OrderEvent): Ордер на исполнение.
            last_candle (pd.Series): Данные свечи, на которой происходит исполнение.
                                     (Это свеча T, следующая за свечой генерации сигнала T-1).

        Raises:
            ValueError: Если не переданы данные свечи.
        """
        if last_candle is None:
            raise ValueError("Для симуляции исполнения необходимы данные последней свечи.")

        # 1. Определение базовой цены
        if event.price is not None:
            # Исполнение отложенного ордера (SL/TP)
            ideal_price = event.price
        else:
            # Рыночный вход (Market Order)
            ideal_price = last_candle['open']

        # 2. Симуляция проскальзывания
        execution_price = self._simulate_slippage(
            ideal_price=ideal_price,
            quantity=event.quantity,
            direction=event.direction,
            candle_volume=last_candle['volume']
        )

        # 3. Расчет комиссии
        # Считается от объема сделки (quantity * price)
        commission = execution_price * event.quantity * self.commission_rate

        # 4. Генерация события исполнения
        fill_event = FillEvent(
            timestamp=event.timestamp,  # Используем время инициации ордера
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