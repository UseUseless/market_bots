"""
Модуль валидации правил инструмента (Exchange Rules).

Отвечает за приведение "математического" размера позиции к "биржевому" формату.
Например, если RiskManager рассчитал объем 153.456 акций, а биржа требует
кратность лоту 10 и шаг 1, этот модуль скорректирует объем до 150.
"""

from typing import Dict, Any


class InstrumentRulesValidator:
    """
    Валидатор и корректор объема ордера.

    Инкапсулирует спецификации инструмента (лотность, шаг цены, мин. объем)
    и предоставляет методы для "нормализации" значений перед отправкой ордера.

    Attributes:
        lot_size (int): Размер лота (сколько единиц актива в одном лоте).
                        Для акций РФ обычно 10, 100 и т.д. Для крипты часто 1.
        qty_step (float): Минимальный шаг изменения объема (например, 0.001 для BTC).
        min_order_qty (float): Минимально допустимый объем ордера.
        precision (int): Количество знаков после запятой для округления (вычисляется из qty_step).
    """

    def __init__(self, instrument_info: Dict[str, Any]):
        """
        Инициализирует правила для конкретного инструмента.

        Args:
            instrument_info (Dict[str, Any]): Словарь метаданных инструмента.
                Ожидаемые ключи: "lot_size", "qty_step", "min_order_qty".
        """
        self.lot_size = int(instrument_info.get("lot_size", 1))
        self.qty_step = float(instrument_info.get("qty_step", 1.0))
        self.min_order_qty = float(instrument_info.get("min_order_qty", self.lot_size))

        # Предварительный расчет точности округления, чтобы не делать это
        # в runtime при каждом ордере.
        self.precision = 0
        step_str = str(self.qty_step)
        if '.' in step_str:
            # "0.001" -> 3 знака
            self.precision = len(step_str.split('.')[1])

    def adjust_quantity(self, quantity_float: float) -> float:
        """
        Корректирует рассчитанное количество под правила биржи.

        Алгоритм:
        1. Округляет вниз до ближайшего шага объема (`qty_step`).
        2. Округляет вниз до целого количества лотов (`lot_size`), если применимо.
        3. Округляет до фиксированного кол-ва знаков после запятой (для устранения float-артефактов).
        4. Проверяет на минимальный размер ордера.

        Args:
            quantity_float (float): "Идеальное" количество, рассчитанное сайзером (например, 123.4567).

        Returns:
            float: Скорректированное количество (например, 120.0), готовое для отправки в API.
                   Возвращает 0.0, если объем меньше минимального.
        """
        # 1. Округляем вниз до ближайшего шага
        if self.qty_step > 0:
            # (123.45 // 0.1) * 0.1 = 1234.0 * 0.1 = 123.4
            adjusted_qty = (quantity_float // self.qty_step) * self.qty_step
        else:
            adjusted_qty = quantity_float

        # 2. Округляем до целого лота (актуально для фондового рынка)
        if self.lot_size > 1:
            num_lots = adjusted_qty // self.lot_size
            adjusted_qty = num_lots * self.lot_size

        # 3. Убираем "хвосты" плавающей точки (например, 100.000000001 -> 100.0)
        if self.precision > 0:
            adjusted_qty = round(adjusted_qty, self.precision)
        else:
            adjusted_qty = int(adjusted_qty)

        # 4. Проверка на минимальный размер
        if adjusted_qty < self.min_order_qty:
            return 0.0

        return adjusted_qty