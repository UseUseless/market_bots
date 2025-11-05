import pytest
import pandas as pd
import os
from datetime import datetime, timedelta, timezone
import pandas_ta as ta
os.environ['MPLBACKEND'] = 'Agg'

@pytest.fixture(scope="session")
def test_data_root(tmp_path_factory):
    """Создает корневую папку 'data' во временной директории для всех тестов сессии."""
    return tmp_path_factory.mktemp("data")

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


@pytest.fixture(scope="session")
def atr_test_data_fixture(test_data_root):
    """Создает данные, где на 5-й свече ATR равен известному значению."""
    time_utc = [datetime(2023, 1, 2, 10, i, tzinfo=timezone.utc) for i in range(10)]
    data = {
        'time': time_utc,
        'open': [100, 102, 101, 103, 105, 106, 107, 108, 109, 110],
        'high': [103, 104, 105, 106, 108, 109, 110, 111, 112, 113],
        'low': [99, 100, 100, 102, 104, 105, 106, 107, 108, 109],
        'close': [102, 101, 103, 105, 106, 107, 108, 109, 110, 111],
        'volume': [1000] * 10
    }
    df = pd.DataFrame(data)
    # Рассчитываем ATR, чтобы он был в данных
    df.ta.atr(length=3, append=True, col_names=('ATR_3',))
    # На 5-й свече (индекс 4) ATR будет известен для расчетов

    data_dir = test_data_root / "tinkoff" / "5min"
    os.makedirs(data_dir, exist_ok=True)
    file_path = data_dir / "ATR_TEST_DATA.parquet"
    df.to_parquet(file_path)

    return {"exchange": "tinkoff", "instrument": "ATR_TEST_DATA", "data_root": test_data_root}