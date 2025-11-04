import subprocess
import os
import pytest
import sys

# --- Дымовые тесты для утилит ---

@pytest.mark.parametrize("exchange, instrument", [
    ("tinkoff", "SBER"),
    ("bybit", "BTCUSDT")
])
def test_download_data_smoke(exchange, instrument, tmp_path):
    """
    Дымовой тест для download_data.py.
    Проверяет, что скрипт успешно запускается и создает файл в правильной
    структуре (data/exchange/interval/) для разных бирж.
    """
    # 1. Подготовка (Arrange)
    # tmp_path - это специальная фикстура pytest, которая создает уникальную
    # временную папку для этого теста. Это гарантирует, что тесты не влияют
    # друг на друга и не загрязняют основной проект.
    data_dir = tmp_path / "data"

    # Формируем команду для запуска скрипта как отдельного процесса.
    command = [
        sys.executable, "download_data.py",
        "--exchange", exchange,
        "--instrument", instrument,
        "--interval", "5min",
        "--days", "1",
        "--data_dir", str(data_dir)  # Передаем путь к нашей временной папке
    ]

    # 2. Действие (Act)
    # Запускаем команду. check=True означает, что если скрипт завершится
    # с ошибкой (код возврата не 0), pytest автоматически провалит тест.
    subprocess.run(command, capture_output=True, text=True, check=True)

    # 3. Проверка (Assert)
    # Собираем ожидаемый путь к файлу в соответствии с новой архитектурой.
    expected_file = data_dir / exchange / "5min" / f"{instrument.upper()}.parquet"
    # Проверяем, что файл действительно был создан по этому пути.
    assert os.path.exists(expected_file), f"Файл {expected_file} не был создан"


# --- Дымовые тесты для основного приложения ---

@pytest.mark.parametrize("rm_type", ["FIXED", "ATR"])
def test_run_smoke(rm_type, test_data_fixture, tmp_path, monkeypatch):
    """
    Дымовой тест для run_backtest.py.
    Проверяет, что основной скрипт бэктеста запускается без ошибок
    с разными риск-менеджерами.
    """
    # 1. Подготовка (Arrange)
    # Получаем информацию о тестовых данных из фикстуры (из conftest.py)
    exchange = test_data_fixture["exchange"]
    instrument = test_data_fixture["instrument"]
    data_root = test_data_fixture["data_root"]

    # monkeypatch - мощный инструмент pytest для временной подмены переменных,
    # функций или настроек. Здесь мы "на лету" подменяем пути в конфиге,
    # чтобы скрипт run_backtest.py читал данные из нашей временной папки с тестовыми
    # данными и писал логи/отчеты тоже во временную папку.
    monkeypatch.setattr("config.PATH_CONFIG", {
        "DATA_DIR": str(data_root),
        "LOGS_DIR": str(tmp_path / "logs"),
        "REPORTS_DIR": str(tmp_path / "reports")
    })

    # Формируем команду, теперь с обязательным аргументом --exchange.
    command = [
        sys.executable, "run_backtest.py",
        "--strategy", "triple_filter",
        "--exchange", exchange,
        "--instrument", instrument,
        "--interval", "5min", # Используем интервал, для которого созданы данные
        "--rm", rm_type
    ]

    # 2. Действие (Act) & 3. Проверка (Assert)
    # Запускаем и автоматически проверяем на ошибки.
    subprocess.run(command, capture_output=True, text=True, check=True)


def test_batch_tester_smoke(test_data_fixture, tmp_path, monkeypatch):
    """
    Дымовой тест для batch_tester.py.
    Проверяет, что скрипт массового тестирования запускается без ошибок.
    """
    # 1. Подготовка (Arrange)
    # Аналогично предыдущему тесту, получаем данные из фикстуры.
    exchange = test_data_fixture["exchange"]
    data_root = test_data_fixture["data_root"]

    # И так же подменяем пути в конфиге.
    monkeypatch.setattr("config.PATH_CONFIG", {
        "DATA_DIR": str(data_root),
        "LOGS_DIR": str(tmp_path / "logs"),
        "REPORTS_DIR": str(tmp_path / "reports")
    })

    # Формируем команду с обязательным аргументом --exchange.
    command = [
        sys.executable, "batch_tester.py",
        "--strategy", "triple_filter",
        "--exchange", exchange,
        "--interval", "5min"
    ]

    # 2. Действие (Act) & 3. Проверка (Assert)
    # Запускаем и автоматически проверяем на ошибки.
    subprocess.run(command, capture_output=True, text=True, check=True)