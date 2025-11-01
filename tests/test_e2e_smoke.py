import subprocess
import os
import pytest
import sys

# Мы создаем список кортежей, где каждый кортеж - это набор аргументов для одного запуска
@pytest.mark.parametrize("exchange, instrument", [
    ("tinkoff", "SBER"),
    ("bybit", "BTCUSDT")])
def test_download_data_smoke(exchange, instrument, tmp_path):
    """
    Дымовой тест для download_data.py. Проверяет, что скрипт запускается
    и создает файл для разных бирж.
    """
    # 1. Подготовка (Arrange)
    # tmp_path - это специальная фикстура pytest, которая создает временную папку
    data_dir = tmp_path / "data"

    command = [
        sys.executable, "download_data.py",
        "--exchange", exchange,
        "--instrument", instrument,
        "--interval", "5min",
        "--days", "1",
        "--data_dir", str(data_dir)
    ]

    subprocess.run(command, capture_output=True, text=True, check=True)

    # Проверка (Assert)
    # Проверяем, что скрипт отработал без ошибок (check=True это уже делает)
    # И что в нашей временной папке появился ожидаемый файл
    expected_file = data_dir / "5min" / f"{instrument.upper()}.parquet"
    assert os.path.exists(expected_file), f"Файл {expected_file} не был создан"


@pytest.mark.parametrize("rm_type, interval", [
    ("FIXED", "5min"),
    ("ATR", "15min")])
def test_run_smoke(rm_type, interval, test_data_fixture, tmp_path, monkeypatch):
    """
    Дымовой тест для run.py. Проверяет запуск с разными RM и интервалами.
    """
    # 1. Подготовка (Arrange)
    # Создаем временные папки для логов и отчетов
    logs_dir = tmp_path / "logs"
    reports_dir = tmp_path / "reports"

    # Создаем фейковый 15-минутный файл данных, если нужно
    original_path = "data/5min/TEST_E2E.parquet"
    if interval == "15min":
        interval_path = f"data/{interval}/TEST_E2E.parquet"
        os.makedirs(f"data/{interval}", exist_ok=True)
        os.replace(original_path, interval_path)

    command = [
        sys.executable, "run.py",
        "--strategy", "triple_filter",
        "--instrument", test_data_fixture,
        "--interval", interval,
        "--rm", rm_type
    ]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("config.PATH_CONFIG", {
        "LOGS_DIR": str(logs_dir),
        "REPORTS_DIR": str(reports_dir)
    })

    # 2. Действие (Act) & 3. Проверка (Assert)
    # check=True автоматически вызовет ошибку, если returncode != 0
    subprocess.run(command, capture_output=True, text=True, check=True)

    # Восстанавливаем файл данных для других тестов
    if interval == "15min":
        os.replace(interval_path, original_path)

def test_batch_tester_smoke(test_data_fixture, tmp_path):
    """Дымовой тест для batch_tester.py."""
    # 1. Подготовка (Arrange)
    logs_dir = tmp_path / "logs"
    reports_dir = tmp_path / "reports"

    command = [
        sys.executable, "batch_tester.py",
        "--strategy", "triple_filter",
        "--interval", "5min"
    ]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("config.PATH_CONFIG", {
        "LOGS_DIR": str(logs_dir),
        "REPORTS_DIR": str(reports_dir)
    })

    # 2. Действие (Act) & 3. Проверка (Assert)
    subprocess.run(command, capture_output=True, text=True, check=True)