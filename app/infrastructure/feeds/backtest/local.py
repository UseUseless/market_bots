"""
Обработчик локальных исторических данных.

Этот модуль отвечает за загрузку, очистку и предобработку исторических данных
из локальных файлов (.parquet) для проведения бэктестов.

Основные функции:
1. Загрузка данных с диска.
2. Нормализация временных меток (UTC).
3. Устранение разрывов в данных (Resampling & Gap Filling).
4. Фильтрация по торговым сессиям (например, исключение выходных и ночей для акций).
"""

import logging
import os
from datetime import time
import pandas as pd

from app.shared.config import config

EXCHANGE_SPECIFIC_CONFIG = config.EXCHANGE_SPECIFIC_CONFIG

logger = logging.getLogger('backtester')


class BacktestDataLoader:
    """
    Загрузчик исторических данных из локальных файлов.

    Обеспечивает подготовку "чистого" DataFrame для движка бэктестинга.
    Гарантирует, что данные непрерывны (нет пропусков свечей) и соответствуют
    заданному интервалу.
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

        Если в данных отсутствуют свечи (например, из-за отсутствия сделок на бирже
        в тихую минуту), этот метод создает искусственные свечи ("Flat candles"),
        чтобы сохранить непрерывность временного ряда. Это критично для корректного
        расчета индикаторов (SMA, EMA).

        Алгоритм заполнения:
        - Close: Берется значение закрытия предыдущей свечи (ffill).
        - Open, High, Low: Приравниваются к Close (свеча без тела и теней).
        - Volume: Устанавливается в 0.

        Args:
            df (pd.DataFrame): Исходный DataFrame.

        Returns:
            pd.DataFrame: DataFrame с непрерывным индексом времени.
        """
        if df.empty:
            return df

        # Работаем с копией, чтобы не менять исходный объект по ссылке
        df = df.copy()
        df.set_index('time', inplace=True)

        # Карта частот для pandas resample
        freq_map = {
            '1min': '1min', '2min': '2min', '3min': '3min', '5min': '5min',
            '10min': '10min', '15min': '15min', '30min': '30min',
            '1hour': '1h', '2hour': '2h', '4hour': '4h', '1day': 'D'
        }
        freq = freq_map.get(self.interval)

        if not freq:
            logger.warning(f"Не удалось определить частоту для resample: '{self.interval}'. Пропуск этапа.")
            return df.reset_index()

        # Правила агрегации: если в одну новую свечу попадает несколько старых (downsampling)
        agg_rules = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }

        if 'turnover' in df.columns:
            agg_rules['turnover'] = 'sum'

        # 1. Создаем полную сетку времени (вставляет NaN там, где нет данных)
        resampled_df = df.resample(freq).agg(agg_rules)

        # 2. Логика заполнения пропусков (Flat Candles)
        # Сначала протягиваем цену закрытия вперед
        resampled_df['close'] = resampled_df['close'].ffill()

        # Остальные цены равны Close (цена стояла на месте)
        resampled_df['open'] = resampled_df['open'].fillna(resampled_df['close'])
        resampled_df['high'] = resampled_df['high'].fillna(resampled_df['close'])
        resampled_df['low'] = resampled_df['low'].fillna(resampled_df['close'])

        # Объем в пустые минуты равен 0
        resampled_df['volume'] = resampled_df['volume'].fillna(0).astype(int)

        if 'turnover' in resampled_df.columns:
            resampled_df['turnover'] = resampled_df['turnover'].fillna(0)

        resampled_df.reset_index(inplace=True)

        # Логирование изменений размера (полезно для отладки качества данных)
        original_rows = len(df)
        filled_rows = len(resampled_df)
        if filled_rows != original_rows:
            logger.info(f"Выравнивание сетки ({self.interval}): {original_rows} -> {filled_rows} строк.")

        return resampled_df

    def _filter_main_session(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Фильтрует данные, оставляя только основную торговую сессию.

        Используется для фондовых рынков (например, MOEX), чтобы исключить
        вечерние/утренние аукционы или выходные дни, если это задано в конфиге.

        Args:
            df (pd.DataFrame): Полный DataFrame.

        Returns:
            pd.DataFrame: Отфильтрованный DataFrame.
        """
        exchange_config = EXCHANGE_SPECIFIC_CONFIG.get(self.exchange)

        # Если время сессии не задано, возвращаем данные как есть (для крипты 24/7)
        if not exchange_config or not exchange_config.get("SESSION_START_UTC"):
            return df

        start_str = exchange_config["SESSION_START_UTC"]
        end_str = exchange_config["SESSION_END_UTC"]

        main_session_start = time.fromisoformat(start_str)
        main_session_end = time.fromisoformat(end_str)

        original_rows = len(df)

        # Фильтрация по времени и дням недели (0-4 = Пн-Пт)
        df_filtered = df[
            (df['time'].dt.time >= main_session_start) &
            (df['time'].dt.time <= main_session_end) &
            (df['time'].dt.dayofweek.isin([0, 1, 2, 3, 4]))
            ].copy()

        filtered_rows = len(df_filtered)
        if original_rows > 0 and filtered_rows < original_rows:
            logger.info(f"Фильтрация по основной сессии: {original_rows} -> {filtered_rows} свечей.")

        return df_filtered

    def load_raw_data(self) -> pd.DataFrame:
        """
        Загружает и подготавливает данные для стратегии.

        Пайплайн обработки:
        1. Чтение Parquet.
        2. Приведение времени к UTC.
        3. Заполнение пропусков (Resampling).
        4. Фильтрация сессий.

        Returns:
            pd.DataFrame: Готовый к использованию DataFrame или пустой DF при ошибке.
        """
        logger.info(f"DataHandler (Local): Чтение данных из {self.file_path}...")

        try:
            if not os.path.exists(self.file_path):
                raise FileNotFoundError

            df = pd.read_parquet(self.file_path)

            if df.empty:
                logger.warning(f"DataHandler (Local): Файл {self.file_path} пуст.")
                return pd.DataFrame()

            # Обработка таймзон: приводим всё к UTC
            if df['time'].dt.tz is None:
                logger.warning("Время в файле без таймзоны. Принудительно устанавливаю UTC.")
                df['time'] = df['time'].dt.tz_localize('UTC')
            else:
                df['time'] = df['time'].dt.tz_convert('UTC')

            # Применение трансформаций
            df_filled = self._resample_and_fill_gaps(df)
            df_final = self._filter_main_session(df_filled)

            logger.info(f"DataHandler (Local): Загружено и обработано {len(df_final)} свечей.")
            return df_final

        except FileNotFoundError:
            logger.error(f"DataHandler (Local): Файл не найден: {self.file_path}")
            logger.error("Подсказка: Скачайте данные через 'python launcher.py' -> 'Управление данными'.")
            return pd.DataFrame()
        except Exception as e:
            logger.error(
                f"DataHandler (Local): Ошибка обработки файла {self.file_path}: {e}",
                exc_info=True
            )
            return pd.DataFrame()