import pytest
import pandas as pd
import os
from datetime import datetime, timedelta
import pandas_ta as ta

@pytest.fixture(scope="session")
def test_data_root(tmp_path_factory):
    """Создает корневую папку 'data' во временной директории для всех тестов сессии."""
    return tmp_path_factory.mktemp("data")

@pytest.fixture(scope="session")
def test_data_fixture(test_data_root):
    """
    Создает parquet-файл с данными для Tinkoff в правильной структуре.
    """
    # 1. Подготовка данных
    num_candles = 250
    base_time = datetime(2023, 1, 1)
    data = {
        'time': [base_time + timedelta(minutes=i * 5) for i in range(num_candles)],
        'open': [100 + i * 0.1 for i in range(num_candles)],
        'high': [101 + i * 0.1 for i in range(num_candles)],
        'low': [99 + i * 0.1 for i in range(num_candles)],
        'close': [100.5 + i * 0.1 for i in range(num_candles)],
        'volume': [1000] * num_candles
    }
    df = pd.DataFrame(data)
    # Рассчитываем индикаторы прямо здесь, чтобы точно знать их значения
    df.ta.ema(length=9, append=True, col_names=('EMA_9',))
    df.ta.ema(length=21, append=True, col_names=('EMA_21',))
    df.ta.ema(length=200, append=True, col_names=('EMA_200',))
    df.ta.sma(length=20, close='volume', append=True, col_names=('Volume_SMA_20',))

    # Искусственно создаем идеальные условия для сигнала на покупку на 220-й свече
    idx = 220
    df.loc[idx, 'close'] = df.loc[idx, 'EMA_200'] + 1  # Цена выше трендового EMA
    df.loc[idx, 'volume'] = df.loc[idx, 'Volume_SMA_20'] + 1  # Объем выше среднего
    # Создаем пересечение быстрых EMA
    df.loc[idx - 1, 'EMA_9'] = 100
    df.loc[idx - 1, 'EMA_21'] = 101  # Была ниже
    df.loc[idx, 'EMA_9'] = 102
    df.loc[idx, 'EMA_21'] = 101  # Стала выше

    # 2. Сохранение файла
    data_dir = test_data_root / "tinkoff" / "5min"
    os.makedirs(data_dir, exist_ok=True)
    file_path = data_dir / "TEST_E2E.parquet"
    df.to_parquet(file_path)

    # Возвращаем словарь с информацией, чтобы тесты знали, где искать данные
    return {"exchange": "tinkoff", "instrument": "TEST_E2E", "data_root": test_data_root}


@pytest.fixture(scope="session")
def perfect_market_data_fixture(test_data_root):
    """
    Создает parquet-файл с идеализированными рыночными данными,
    где гарантированно срабатывают TP для лонга и SL для шорта.
    """
    # 1. Подготовка данных
    base_date = datetime(2023, 1, 2)
    data = {
        'time': [base_date.replace(hour=10, minute=i) for i in range(25)],
        'open': [
            # 0-4: Флэт
            100, 100, 100, 100, 100,
            # 5: Сигнал на покупку, вход по 100
            100,
            # 6-9: Рост
            101, 102, 103, 104,
            # 10: Срабатывание TP на 106
            105,
            # 11-14: Флэт
            106, 106, 106, 106,
            # 15: Сигнал на продажу, вход по 120
            120,
            # 16-19: Падение
            119, 118, 117, 116,
            # 20: Срабатывание SL на 122.4
            121,
            # 21-24: Флэт
            122, 122, 122, 122
        ],
        'high': [
            101, 101, 101, 101, 101, 101, 102, 103, 104, 105,
            106,  # <-- Цена касается TP
            107, 107, 107, 107, 121, 120, 119, 118, 117,
            122.4,  # <-- Цена касается SL
            123, 123, 123, 123
        ],
        'low': [
            99, 99, 99, 99, 99, 99, 100, 101, 102, 103, 104, 105, 105, 105, 105,
            119, 118, 117, 116, 115, 120, 121, 121, 121, 121
        ],
        'close': [
            100, 100, 100, 100, 100, 101, 102, 103, 104, 105, 106, 106, 106, 106,
            120, 119, 118, 117, 116, 121, 122, 122, 122, 122, 122
        ],
        'volume': [1000] * 25
    }
    df = pd.DataFrame(data)

    # 2. Сохранение файла
    data_dir = test_data_root / "tinkoff" / "5min"
    os.makedirs(data_dir, exist_ok=True)
    file_path = data_dir / "PERFECT_DATA.parquet"
    df.to_parquet(file_path)

    # Возвращаем словарь
    return {"exchange": "tinkoff", "instrument": "PERFECT_DATA", "data_root": test_data_root}