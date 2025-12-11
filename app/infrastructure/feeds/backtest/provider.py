"""
Модуль поставщика данных для бэктеста.

Содержит реализацию `MarketDataProvider`, которая эмулирует потоковую передачу данных
на основе статического DataFrame. Это позволяет "проигрывать" историю свеча за свечой.
"""
import logging
import os
from datetime import time
from typing import List

import numpy as np
import pandas as pd

from app.shared.config import config
from app.shared.interfaces import MarketDataProvider

EXCHANGE_SPECIFIC_CONFIG = config.EXCHANGE_SPECIFIC_CONFIG
logger = logging.getLogger('backtester')


class BacktestDataLoader:
    """
    Загрузчик исторических данных из локальных файлов.

    Обеспечивает подготовку "чистого" DataFrame для движка бэктестинга.
    Умеет загружать данные, заполнять пропуски и нарезать историю на периоды.
    """

    def __init__(self, exchange: str, instrument_id: str, interval_str: str, data_path: str):
        """
        Инициализирует обработчик.

        Args:
            exchange (str): Название биржи (tinkoff/bybit).
            instrument_id (str): Тикер инструмента (BTCUSDT, SBER).
            interval_str (str): Интервал свечей (1min, 1hour).
            data_path (str): Путь к корневой папке с данными.
        """
        self.exchange = exchange
        self.instrument_id = instrument_id
        self.interval = interval_str
        self.data_path = data_path
        self.file_path = os.path.join(
            self.data_path, self.exchange, self.interval, f"{instrument_id.upper()}.parquet"
        )

    def _resample_and_fill_gaps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Выравнивает временную сетку и заполняет пропуски (Gap Filling).
        """
        if df.empty:
            return df

        df = df.copy()
        df.set_index('time', inplace=True)

        freq_map = {
            '1min': '1min', '2min': '2min', '3min': '3min', '5min': '5min',
            '10min': '10min', '15min': '15min', '30min': '30min',
            '1hour': '1h', '2hour': '2h', '4hour': '4h', '1day': 'D'
        }
        freq = freq_map.get(self.interval)

        if not freq:
            logger.warning(f"Не удалось определить частоту для resample: '{self.interval}'. Пропуск этапа.")
            return df.reset_index()

        agg_rules = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }

        if 'turnover' in df.columns:
            agg_rules['turnover'] = 'sum'

        resampled_df = df.resample(freq).agg(agg_rules)

        resampled_df['close'] = resampled_df['close'].ffill()
        resampled_df['open'] = resampled_df['open'].fillna(resampled_df['close'])
        resampled_df['high'] = resampled_df['high'].fillna(resampled_df['close'])
        resampled_df['low'] = resampled_df['low'].fillna(resampled_df['close'])
        resampled_df['volume'] = resampled_df['volume'].fillna(0).astype(int)

        if 'turnover' in resampled_df.columns:
            resampled_df['turnover'] = resampled_df['turnover'].fillna(0)

        resampled_df.reset_index(inplace=True)
        return resampled_df

    def _filter_main_session(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Фильтрует данные, оставляя только основную торговую сессию.
        """
        exchange_config = EXCHANGE_SPECIFIC_CONFIG.get(self.exchange)
        if not exchange_config or not exchange_config.get("SESSION_START_UTC"):
            return df

        start_str = exchange_config["SESSION_START_UTC"]
        end_str = exchange_config["SESSION_END_UTC"]

        main_session_start = time.fromisoformat(start_str)
        main_session_end = time.fromisoformat(end_str)

        df_filtered = df[
            (df['time'].dt.time >= main_session_start) &
            (df['time'].dt.time <= main_session_end) &
            (df['time'].dt.dayofweek.isin([0, 1, 2, 3, 4]))
        ].copy()

        return df_filtered

    def load_raw_data(self) -> pd.DataFrame:
        """
        Загружает и подготавливает полный DataFrame данных.

        Returns:
            pd.DataFrame: Готовый к использованию DataFrame или пустой DF при ошибке.
        """
        # logger.info(f"Загрузка данных: {self.file_path}...")
        # Убрал лишний лог, чтобы не спамить при WFO на 50 инструментов

        try:
            if not os.path.exists(self.file_path):
                return pd.DataFrame()

            df = pd.read_parquet(self.file_path)
            if df.empty:
                return pd.DataFrame()

            if df['time'].dt.tz is None:
                df['time'] = df['time'].dt.tz_localize('UTC')
            else:
                df['time'] = df['time'].dt.tz_convert('UTC')

            df_filled = self._resample_and_fill_gaps(df)
            df_final = self._filter_main_session(df_filled)

            return df_final

        except Exception as e:
            logger.error(f"Ошибка чтения {self.file_path}: {e}")
            return pd.DataFrame()

    def load_and_split(self, total_periods: int) -> List[pd.DataFrame]:
        """
        Загружает данные и разбивает их на N равных частей для WFO.

        Это перенесенная логика из бывшего engine/optimization/splitter.py.
        Теперь инфраструктура сама отвечает за предоставление "чанков" данных.

        Args:
            total_periods (int): Количество частей, на которые нужно разбить историю.

        Returns:
            List[pd.DataFrame]: Список датафреймов.
        """
        df = self.load_raw_data()

        if df.empty:
            return []

        # Используем numpy для разбиения (аналог того, что было в splitter.py)
        # np.array_split корректно работает с DataFrame
        try:
            chunks = np.array_split(df, total_periods)
            # Конвертируем обратно в чистый список (numpy возвращает массив объектов)
            return [chunk for chunk in chunks if not chunk.empty]
        except Exception as e:
            logger.error(f"Ошибка при нарезке данных для {self.instrument_id}: {e}")
            return []


class BacktestDataProvider(MarketDataProvider):
    """
    Эмулятор потока рыночных данных.

    Работает по принципу курсора: хранит весь DataFrame в памяти, но открывает
    доступ к нему последовательно. Стратегия "видит" только те данные, которые
    находятся до текущего момента времени (индекса).

    Attributes:
        _data (pd.DataFrame): Полный набор исторических данных.
        _interval (str): Таймфрейм данных.
        _current_index (int): Указатель на "текущую" свечу в симуляции.
        _max_index (int): Последний доступный индекс в массиве.
    """

    def __init__(self, data: pd.DataFrame, interval: str):
        """
        Инициализирует фид.

        Args:
            data (pd.DataFrame): DataFrame с историей и предрасчитанными индикаторами.
                                 Должен быть отсортирован по времени.
            interval (str): Таймфрейм (например, '5min').
        """
        # Сбрасываем индекс, чтобы работать с integer-location (iloc) от 0 до N
        self._data = data.reset_index(drop=True)
        self._interval = interval
        self._current_index = -1
        self._max_index = len(self._data) - 1

    @property
    def interval(self) -> str:
        """Возвращает текущий таймфрейм."""
        return self._interval

    def next(self) -> bool:
        """
        Перемещает курсор времени на одну свечу вперед.

        Этот метод вызывает движок бэктеста в основном цикле.

        Returns:
            bool: True, если данные еще есть (симуляция продолжается).
                  False, если достигнут конец истории.
        """
        if self._current_index < self._max_index:
            self._current_index += 1
            return True
        return False

    def get_current_candle(self) -> pd.Series:
        """
        Возвращает данные "текущей" свечи (на которую указывает курсор).

        Returns:
            pd.Series: Строка DataFrame.

        Raises:
            ValueError: Если метод вызван до первого вызова `next()`.
        """
        if self._current_index < 0:
            raise ValueError("Feed не запущен. Сначала вызовите next().")
        return self._data.iloc[self._current_index]

    def get_history(self, length: int) -> pd.DataFrame:
        """
        Возвращает срез исторических данных относительно текущего момента.

        Диапазон: `[текущий_индекс - length + 1 : текущий_индекс + 1]`.
        То есть возвращает `length` последних свечей, включая текущую.

        Args:
            length (int): Глубина запрашиваемой истории.

        Returns:
            pd.DataFrame: Копия среза данных.
        """
        if self._current_index < 0:
            return pd.DataFrame()

        # Вычисляем начало окна. Не может быть меньше 0.
        start_index = max(0, self._current_index - length + 1)
        end_index = self._current_index + 1

        # Возвращаем копию, чтобы стратегия случайно не изменила исходные данные
        return self._data.iloc[start_index:end_index].copy()
