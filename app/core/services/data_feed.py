import pandas as pd
import logging
from typing import List, Optional

from app.core.services.feature_engine import FeatureEngine

logger = logging.getLogger(__name__)


class DataFeedService:
    """
    Сервис, который превращает поток сырых свечей в поток данных, готовых для стратегии.
    Отвечает за:
    1. Хранение скользящего окна истории (History Buffer).
    2. Инкрементальный расчет индикаторов (через FeatureEngine).
    """

    def __init__(self, feature_engine: FeatureEngine, required_indicators: List[dict], max_len: int = 500):
        self.feature_engine = feature_engine
        self.required_indicators = required_indicators
        self.max_len = max_len
        # Храним данные как список словарей (быстрее для append, чем DataFrame)
        self._buffer: List[dict] = []

    def warm_up(self, historical_data: pd.DataFrame):
        """Инициализация буфера историей."""
        if historical_data.empty:
            logger.warning("DataFeed: Передана пустая история для разогрева!")
            return

        # Берем хвост и конвертируем в список словарей
        # records - это самый быстрый способ конвертации для наших целей
        self._buffer = historical_data.tail(self.max_len).to_dict('records')
        logger.info(f"DataFeed: Буфер инициализирован ({len(self._buffer)} свечей).")

    def add_candle_and_get_window(self, candle: pd.Series) -> Optional[pd.DataFrame]:
        """
        Добавляет свечу, пересчитывает индикаторы и возвращает мини-окно данных
        (обычно 2 последние свечи: [prev, current]) для принятия решений.
        """
        # 1. Добавляем в буфер
        # Превращаем серию в словарь. Важно: предполагаем, что ключи (open, close...) правильные.
        self._buffer.append(candle.to_dict())

        # Поддерживаем фиксированный размер, удаляя старое
        if len(self._buffer) > self.max_len:
            self._buffer.pop(0)

        # 2. Если буфер слишком мал для расчета индикаторов - выходим.
        # 50 - эмпирический минимум. Если у тебя SMA_200, этого не хватит,
        # но warm_up должен был заполнить буфер заранее.
        if len(self._buffer) < 50:
            return None

        # 3. Конвертируем в DF для FeatureEngine
        # Это операция O(N), где N=max_len. Для N=500 это очень быстро.
        df = pd.DataFrame(self._buffer)

        # 3.1 Гарантируем правильные типы данных (Защита от дурака/API)
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'])

        # Принудительно конвертируем цены в float, чтобы pandas-ta не ругался
        cols_to_float = ['open', 'high', 'low', 'close', 'volume']
        for col in cols_to_float:
            if col in df.columns:
                df[col] = df[col].astype(float)

        try:
            # 4. Считаем индикаторы (FeatureEngine работает inplace, изменяя df)
            self.feature_engine.add_required_features(df, self.required_indicators)

            # 5. Возвращаем последние 2 строки (Previous + Current)
            # .copy() важен, чтобы передать чистый объект без ссылок на исходный df
            return df.tail(2).copy()

        except Exception as e:
            logger.error(f"DataFeed: Ошибка расчета индикаторов: {e}", exc_info=True)
            return None