import pandas as pd
import logging
from tqdm import tqdm
from typing import Dict, List, Tuple, Any

from app.core.engine.optimization.splitter import split_data_by_periods
from app.infrastructure.feeds.local import HistoricLocalDataHandler
from config import PATH_CONFIG

logger = logging.getLogger(__name__)


class WFODataPreparer:
    """
    Отвечает исключительно за загрузку и нарезку данных для Walk-Forward Optimization.
    """

    def __init__(self, settings: Dict[str, Any]):
        """
        Инициализирует подготовитель данных с настройками оптимизации.

        :param settings: Словарь с настройками, содержащий 'instrument_list',
                         'exchange', 'interval', 'total_periods', 'train_periods',
                         'test_periods'.
        """
        self.settings = settings

    def prepare(self) -> Tuple[Dict[str, List[pd.DataFrame]], int]:
        """
        Загружает, нарезает данные и проверяет их на достаточность для WFO.

        :return: Кортеж, содержащий:
                 - Словарь, где ключ - инструмент, значение - список его периодов (DataFrame).
                 - Количество шагов (сдвигов окна), которые можно будет сделать.
        :raises FileNotFoundError: Если не удалось загрузить данные ни для одного инструмента.
        :raises ValueError: Если данных недостаточно для проведения WFO с заданными параметрами.
        """
        logger.info("--- Предварительная загрузка и нарезка данных ---")
        all_instrument_periods = {}
        instrument_list = self.settings["instrument_list"]

        for instrument in tqdm(instrument_list, desc="Подготовка данных"):
            data_handler = HistoricLocalDataHandler(
                exchange=self.settings["exchange"],
                instrument_id=instrument,
                interval_str=self.settings["interval"],
                data_path=PATH_CONFIG["DATA_DIR"]
            )
            full_dataset = data_handler.load_raw_data()
            if full_dataset.empty:
                logger.warning(f"Не удалось загрузить данные для {instrument}. Пропускаем.")
                continue

            # Делегируем нарезку функции из splitter.py
            all_instrument_periods[instrument] = split_data_by_periods(
                full_dataset, self.settings["total_periods"]
            )

        if not all_instrument_periods:
            raise FileNotFoundError("Не удалось загрузить данные ни для одного инструмента из списка.")

        # Проверяем достаточность данных на примере первого инструмента
        first_instrument_periods = next(iter(all_instrument_periods.values()))
        num_steps = (
                len(first_instrument_periods)
                - self.settings["train_periods"]
                - self.settings["test_periods"] + 1
        )

        if num_steps <= 0:
            raise ValueError(
                "Недостаточно данных для WFO с заданными параметрами. "
                "Уменьшите train_periods/test_periods или увеличьте total_periods."
            )

        return all_instrument_periods, num_steps