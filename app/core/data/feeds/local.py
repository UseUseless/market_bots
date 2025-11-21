import logging
import os
from datetime import time

import pandas as pd

from config import EXCHANGE_SPECIFIC_CONFIG

logger = logging.getLogger('backtester')

class HistoricLocalDataHandler:
    """
    Читает локальные Parquet-файлы из структурированной папки (data/exchange/interval),
    выравнивает временную сетку для устранения гэпов,
    фильтрует их для основной торговой сессии (если нужно) и создаёт pandas df.
    """

    def __init__(self, exchange: str, instrument_id: str, interval_str: str,
                 data_path: str):
        self.exchange = exchange
        self.instrument_id = instrument_id
        self.interval = interval_str
        self.data_path = data_path
        self.file_path = os.path.join(self.data_path, self.exchange, self.interval,
                                      f"{instrument_id.upper()}.parquet")

    def _resample_and_fill_gaps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Выравнивает временную сетку, корректно агрегирует данные
        и заполняет пропуски (gaps) внутри торговой сессии.
        """
        if df.empty:
            return df

        df.set_index('time', inplace=True)

        # Карта частот для pandas
        freq_map = {
            '1min': '1min', '2min': '2min', '3min': '3min', '5min': '5min', '10min': '10min',
            '15min': '15min', '30min': '30min', '1hour': '1h', '2hour': '2h',
            '4hour': '4h', '1day': 'D'
        }
        freq = freq_map.get(self.interval)

        if not freq:
            logger.warning(f"Не удалось определить частоту для resample: '{self.interval}'.")
            return df.reset_index()

        # Если мы собираем минутки в 5-минутки, или просто выравниваем сетку:
        # - Объем должен суммироваться.
        # - Хай и Лоу должны искаться реальные.
        agg_rules = {
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }

        # Поддержка turnover (оборота), если он есть в данных
        if 'turnover' in df.columns:
            agg_rules['turnover'] = 'sum'

        # Применяем правила. Это создает сетку с NaNs там, где данных нет.
        resampled_df = df.resample(freq).agg(agg_rules)

        # Логика: Если свечи не было (биржа работала, но сделок не было, или разрыв связи),
        # цена остается прежней, а объем равен 0.
        # Сначала протягиваем цену закрытия (Close) вперед
        resampled_df['close'] = resampled_df['close'].ffill()

        # Остальные цены для "пустой" свечи должны быть равны Close (флэт)
        resampled_df['open'] = resampled_df['open'].fillna(resampled_df['close'])
        resampled_df['high'] = resampled_df['high'].fillna(resampled_df['close'])
        resampled_df['low'] = resampled_df['low'].fillna(resampled_df['close'])

        # Объем для пустой свечи — это 0 (а не объем прошлой свечи!)
        resampled_df['volume'] = resampled_df['volume'].fillna(0).astype(int)

        if 'turnover' in resampled_df.columns:
            resampled_df['turnover'] = resampled_df['turnover'].fillna(0)

        resampled_df.reset_index(inplace=True)

        # Логируем только если размер изменился (реально были дырки или склейка)
        original_rows = len(df)
        filled_rows = len(resampled_df)
        if filled_rows != original_rows:
            logger.info(f"Выравнивание сетки ({self.interval}): {original_rows} -> {filled_rows} строк.")

        return resampled_df

    def _filter_main_session(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Фильтрует DataFrame, оставляя только данные основной торговой сессии,
        если она определена в конфиге для данной биржи.
        """
        exchange_config = EXCHANGE_SPECIFIC_CONFIG.get(self.exchange)
        if not exchange_config or not exchange_config.get("SESSION_START_UTC"):
            logger.info(f"Фильтрация по сессии для биржи '{self.exchange}' не применяется (24/7).")
            return df

        start_str = exchange_config["SESSION_START_UTC"]
        end_str = exchange_config["SESSION_END_UTC"]

        main_session_start = time.fromisoformat(start_str)
        main_session_end = time.fromisoformat(end_str)

        original_rows = len(df)
        df['time'] = pd.to_datetime(df['time'])
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
        Загружает данные из локального Parquet файла, обрабатывает гэпы и применяет фильтрацию.
        """
        logger.info(f"DataHandler (Local): Чтение данных из файла {self.file_path}...")
        try:
            df = pd.read_parquet(self.file_path)
            if df.empty:
                logger.warning(f"DataHandler (Local): Файл {self.file_path} пуст.")
                return pd.DataFrame()

            logger.info(f"DataHandler (Local): Успешно загружено {len(df)} свечей из файла.")

            if df['time'].dt.tz is None:
                logger.warning("Время в локальном файле не имеет таймзоны. Принудительно локализуется в UTC.")
                df['time'] = df['time'].dt.tz_localize('UTC')
            else:
                # Если таймзона уже есть, просто конвертируем ее в UTC на всякий случай
                df['time'] = df['time'].dt.tz_convert('UTC')

            df_filled = self._resample_and_fill_gaps(df)
            df_final = self._filter_main_session(df_filled)

            return df_final

        except FileNotFoundError:
            logger.error(f"DataHandler (Local): Файл не найден: {self.file_path}")
            logger.error("Убедитесь, что вы скачали данные с помощью download_data.py")
            return pd.DataFrame()
        except Exception as e:
            logger.error(
                f"DataHandler (Local): Непредвиденная ошибка при чтении или обработке файла {self.file_path}: {e}",
                exc_info=True)
            return pd.DataFrame()
