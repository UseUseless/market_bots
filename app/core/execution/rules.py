from typing import Dict, Any

class InstrumentRulesValidator:
    """
    Класс, инкапсулирующий логику корректировки размера ордера
    в соответствии с правилами конкретного инструмента (биржи).

    Он берет "идеальное" рассчитанное количество и приводит его к виду,
    допустимому для отправки на биржу (учитывает лотность, шаг и мин. объем).
    """

    def __init__(self, instrument_info: Dict[str, Any]):
        """
        Инициализирует валидатор правилами для конкретного инструмента.

        :param instrument_info: Словарь с метаданными, загруженный из .json файла.
        """
        self.lot_size = int(instrument_info.get("lot_size", 1))
        self.qty_step = float(instrument_info.get("qty_step", 1.0))
        self.min_order_qty = float(instrument_info.get("min_order_qty", self.lot_size))

    def adjust_quantity(self, quantity_float: float) -> float:
        """
        Корректирует рассчитанное количество лотов.

        :param quantity_float: "Идеальное" количество, рассчитанное сайзером.
        :return: Скорректированное количество, готовое для ордера, или 0.
        """
        # 1. Округляем вниз до ближайшего шага количества (qty_step)
        if self.qty_step > 0:
            adjusted_qty = (quantity_float // self.qty_step) * self.qty_step
        else:
            # На случай, если шаг равен 0, чтобы избежать деления на ноль
            adjusted_qty = quantity_float

        # 2. Для инструментов с лотностью > 1 (акции), округляем до целого числа лотов
        if self.lot_size > 1:
            num_lots = adjusted_qty // self.lot_size
            adjusted_qty = num_lots * self.lot_size

        # Округляем финальное значение до точности шага, чтобы убрать "хвосты" float
        if '.' in str(self.qty_step):
            decimal_places = len(str(self.qty_step).split('.')[1])
            adjusted_qty = round(adjusted_qty, decimal_places)

        # 3. Проверяем на минимальный размер ордера
        if adjusted_qty < self.min_order_qty:
            return 0.0

        # Конвертируем в int, если это возможно без потери точности
        if adjusted_qty == int(adjusted_qty):
            return int(adjusted_qty)

        return adjusted_qty
