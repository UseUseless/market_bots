import pytest
import pandas as pd
import os
from datetime import datetime, timedelta
import pandas_ta as ta


@pytest.fixture(scope="session")
def test_data_fixture():
    """
    Создает небольшой parquet-файл с данными, которые гарантированно
    вызовут один сигнал на покупку и один на продажу у TripleFilterStrategy.
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
    data_dir = "data/5min"
    os.makedirs(data_dir, exist_ok=True)
    file_path = os.path.join(data_dir, "TEST_E2E.parquet")
    df.to_parquet(file_path)

    # 3. Передача управления тесту
    yield "TEST_E2E"  # Возвращаем тикер для использования в тесте

    # 4. Очистка после теста
    os.remove(file_path)