"""
Модуль подготовки данных для оптимизации (Data Preparer).

Отвечает за загрузку исторических данных для списка инструментов и их
предварительную нарезку на периоды (Periods) согласно настройкам WFO.
Это позволяет загрузить данные с диска один раз и переиспользовать их
на каждом шаге оптимизации.
"""

import pandas as pd
import logging
from tqdm import tqdm
from typing import Dict, List, Tuple, Any

from app.core.engine.optimization.splitter import split_data_by_periods
from app.infrastructure.feeds.local import HistoricLocalDataHandler
from app.shared.config import config

PATH_CONFIG = config.PATH_CONFIG

logger = logging.getLogger(__name__)


class WFODataPreparer:
    """
    Подготовитель данных для Walk-Forward Optimization.

    Инкапсулирует логику массовой загрузки и валидации данных перед запуском
    тяжелого процесса оптимизации.

    Attributes:
        data_settings (Dict[str, Any]): Настройки данных и параметров WFO.
            Ожидаемые ключи: 'instrument_list', 'exchange', 'interval',
            'total_periods', 'train_periods', 'test_periods'.
    """

    def __init__(self, data_settings: Dict[str, Any]):
        """
        Инициализирует подготовитель.

        Args:
            data_settings (Dict[str, Any]): Словарь конфигурации.
        """
        self.data_settings = data_settings

    def prepare(self) -> Tuple[Dict[str, List[pd.DataFrame]], int]:
        """
        Выполняет загрузку и нарезку данных.

        Алгоритм:
        1. Итерируется по списку инструментов (`instrument_list`).
        2. Загружает историю через `HistoricLocalDataHandler`.
        3. Разбивает историю на N равных частей (`total_periods`).
        4. Рассчитывает количество доступных шагов WFO (`num_steps`).

        Returns:
            Tuple:
                1. Dict[str, List[pd.DataFrame]]: Словарь {Тикер: [Список_Периодов]}.
                2. int: Количество возможных шагов WFO (сдвигов окна).

        Raises:
            FileNotFoundError: Если не удалось загрузить данные ни для одного инструмента.
            ValueError: Если данных недостаточно для формирования хотя бы одного
                окна обучения и теста (Train + Test > Total).
        """
        logger.info("--- Предварительная загрузка и нарезка данных ---")

        all_instrument_periods = {}
        instrument_list = self.data_settings["instrument_list"]

        # Используем tqdm для отображения прогресса загрузки
        for instrument in tqdm(instrument_list, desc="Подготовка данных"):
            data_handler = HistoricLocalDataHandler(
                exchange=self.data_settings["exchange"],
                instrument_id=instrument,
                interval_str=self.data_settings["interval"],
                data_path=PATH_CONFIG["DATA_DIR"]
            )

            full_dataset = data_handler.load_raw_data()

            if full_dataset.empty:
                logger.warning(f"Не удалось загрузить данные для {instrument}. Пропускаем.")
                continue

            # Нарезка данных на равные части
            all_instrument_periods[instrument] = split_data_by_periods(
                full_dataset, self.data_settings["total_periods"]
            )

        if not all_instrument_periods:
            raise FileNotFoundError("Не удалось загрузить данные ни для одного инструмента из списка.")

        # Расчет количества шагов на примере первого успешно загруженного инструмента.
        # Предполагается, что разбиение по количеству частей дает одинаковое
        # число периодов для всех инструментов (total_periods).
        first_instrument_periods = next(iter(all_instrument_periods.values()))

        num_steps = (
                len(first_instrument_periods)
                - self.data_settings["train_periods"]
                - self.data_settings["test_periods"] + 1
        )

        if num_steps <= 0:
            raise ValueError(
                "Недостаточно данных для WFO с заданными параметрами. "
                "Уменьшите train_periods/test_periods или увеличьте total_periods."
            )

        return all_instrument_periods, num_steps