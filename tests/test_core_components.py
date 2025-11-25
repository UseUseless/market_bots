import pandas as pd
import pytest
from queue import Queue
from datetime import datetime

from app.services.data_provider.feeds.local import HistoricLocalDataHandler


@pytest.fixture
def sample_unfiltered_data() -> pd.DataFrame:
    """
    Создает DataFrame с данными, включающими свечи вне основной сессии и в выходные.
    Основная сессия для теста: 06:50 - 15:30 UTC.
    """
    # Пятница, 2023-01-06
    friday_times = [
        datetime(2023, 1, 6, 6, 49),  # <-- ИСПРАВЛЕНИЕ: До сессии (06:49)
        datetime(2023, 1, 6, 7, 0),   # Внутри сессии
        datetime(2023, 1, 6, 12, 0),  # Внутри сессии
        datetime(2023, 1, 6, 15, 30), # Внутри сессии (включительно)
        datetime(2023, 1, 6, 15, 31), # После сессии
    ]
    # Суббота, 2023-01-07
    saturday_times = [
        datetime(2023, 1, 7, 12, 0),  # Выходной день
    ]

    all_times = friday_times + saturday_times

    data = {
        'time': all_times,
        'open': [100] * len(all_times),
        'high': [100] * len(all_times),
        'low': [100] * len(all_times),
        'close': [100] * len(all_times),
        'volume': [1000] * len(all_times),
    }
    return pd.DataFrame(data)


def test_main_session_filter(sample_unfiltered_data):
    """
    Проверяет, что фильтр _filter_main_session в DataHandler
    корректно отбирает свечи основной торговой сессии.
    """
    # 1. ПОДГОТОВКА (Arrange)
    # Создаем экземпляр DataHandler (путь к файлу не важен, т.к. мы не будем его читать)
    # Важно, что мы передаем exchange='tinkoff', чтобы фильтр активировался
    data_handler = HistoricLocalDataHandler(
        events_queue=Queue(),
        exchange='tinkoff',
        instrument_id='TEST',
        interval_str='5min',
        data_path='dummy/path'  # Не используется
    )

    unfiltered_df = sample_unfiltered_data

    # 2. ДЕЙСТВИЕ (Act)
    # Вызываем приватный метод, который мы хотим протестировать
    filtered_df = data_handler._filter_main_session(unfiltered_df)

    # 3. ПРОВЕРКА (Assert)
    # Ожидаем, что останется 3 свечи: 07:00, 12:00, 15:30
    assert len(filtered_df) == 3

    # Проверяем, что временные метки оставшихся свечей верны
    result_times = filtered_df['time'].dt.time.tolist()

    # Конвертируем время в строки для простого сравнения
    result_times_str = [t.strftime('%H:%M:%S') for t in result_times]

    assert '07:00:00' in result_times_str
    assert '12:00:00' in result_times_str
    assert '15:30:00' in result_times_str

    # Проверяем, что "плохие" свечи были удалены
    assert '06:59:00' not in result_times_str
    assert '15:31:00' not in result_times_str

    # Проверяем, что свеча из выходного дня была удалена
    # (проверяем по дате, а не по времени)
    saturday_date = pd.to_datetime('2023-01-07').date()
    assert all(d.date() != saturday_date for d in filtered_df['time'])