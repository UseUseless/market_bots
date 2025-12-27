"""
Модуль расчета технических индикаторов.

Содержит класс `FeatureEngine`, который выступает оберткой над библиотекой `pandas-ta`.
Реализован гибридный подход: универсальный метод для простых индикаторов
и явные методы для сложных (с множественным выводом).
"""

import logging
from typing import List, Dict, Any

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)


class FeatureEngine:
    """
    Сервис для расчета технических индикаторов.
    
    Выступает адаптером между конфигурацией стратегии и библиотекой pandas-ta.
    """

    def add_required_features(self, data: pd.DataFrame, requirements: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Добавляет в DataFrame запрошенные индикаторы in-place (модифицирует переданный объект).

        Args:
            data (pd.DataFrame): Исходные исторические данные (OHLCV).
            requirements (List[Dict[str, Any]]): Список конфигураций индикаторов.
                Пример: `[{"name": "sma", "params": {"length": 20}}]`.

        Returns:
            pd.DataFrame: Обогащенный DataFrame с добавленными колонками индикаторов.
        """
        if data.empty or not requirements:
            return data

        for req in requirements:
            name = req.get("name", "").lower()
            params = req.get("params", {}).copy()  # Копия, чтобы не мутировать исходный конфиг

            # 1. Сначала ищем явный метод (для сложных случаев типа BBands, Donchian)
            handler = getattr(self, f"_calculate_{name}", None)

            if handler:
                try:
                    handler(data, **params)
                except TypeError as e:
                    logger.error(
                        f"FeatureEngine TypeError: Ошибка параметров для {name}: {e}. "
                    )
                except Exception as e:
                    logger.error(f"FeatureEngine: Ошибка расчета {name}: {e}")
            
            # 2. Если явного метода нет, пытаемся вызвать pandas-ta напрямую (Universal Dispatch)
            # Это работает для SMA, EMA, RSI, ATR и большинства простых индикаторов.
            elif hasattr(data.ta, name):
                try:
                    # Формируем имя колонки вручную, чтобы гарантировать формат "NAME_LENGTH"
                    # Например: SMA_20.
                    length = params.get('length')
                    
                    # Если length не передан, pandas-ta использует дефолт,
                    # но мы не сможем сформировать красивое имя колонки.
                    custom_name = f"{name.upper()}_{length}" if length else None
                    
                    # Удаляем старую колонку во избежание дублей (pandas-ta может добавить суффиксы)
                    if custom_name and custom_name in data.columns:
                        data.drop(columns=[custom_name], inplace=True)
                    
                    # Вызов библиотеки
                    # col_names переопределяет имя выходной колонки
                    col_args = {'col_names': (custom_name,)} if custom_name else {}
                    
                    getattr(data.ta, name)(append=True, **params, **col_args)
                    
                except Exception as e:
                    logger.error(f"FeatureEngine: Ошибка в динамическом расчете {name}: {e}")
            else:
                logger.warning(f"FeatureEngine: Неизвестный индикатор '{name}'")

        return data

    def _calculate_bbands(self, data: pd.DataFrame, length: int, std: float):
        """
        Рассчитывает Полосы Боллинджера (Bollinger Bands).

        Args:
            data (pd.DataFrame): Исходные данные.
            length (int): Период скользящей средней.
            std (float): Количество стандартных отклонений.
        
        Outputs:
            Создает колонки: BBL_{length}, BBM_{length}, BBU_{length}, BBB_{length}, BBP_{length}.
        """
        col_names = (
            f'BBL_{length}', f'BBM_{length}', f'BBU_{length}',
            f'BBB_{length}', f'BBP_{length}'
        )
        # Очистка старых данных
        data.drop(columns=[c for c in col_names if c in data.columns], inplace=True)
        
        data.ta.bbands(length=length, std=std, append=True, col_names=col_names)

    def _calculate_donchian(self, data: pd.DataFrame, lower_length: int, upper_length: int):
        """
        Рассчитывает Каналы Дончиана (Donchian Channels).

        Args:
            data (pd.DataFrame): Исходные данные.
            lower_length (int): Период для нижней границы.
            upper_length (int): Период для верхней границы.

        Outputs:
            Создает колонки: DCL_{upper_length}, DCM_{upper_length}, DCU_{upper_length}.
        """
        col_names = (
            f'DCL_{upper_length}', f'DCM_{upper_length}', f'DCU_{upper_length}'
        )
        data.drop(columns=[c for c in col_names if c in data.columns], inplace=True)
        
        data.ta.donchian(lower_length=lower_length, upper_length=upper_length, append=True, col_names=col_names)

    def _calculate_adx(self, data: pd.DataFrame, length: int):
        """
        Рассчитывает индекс направленного движения (ADX).

        Args:
            data (pd.DataFrame): Исходные данные.
            length (int): Период сглаживания.

        Outputs:
            Создает колонки: ADX_{length}, DMP_{length}, DMN_{length}.
        """
        col_names = (f'ADX_{length}', f'DMP_{length}', f'DMN_{length}')
        data.drop(columns=[c for c in col_names if c in data.columns], inplace=True)
        
        data.ta.adx(length=length, append=True, col_names=col_names)