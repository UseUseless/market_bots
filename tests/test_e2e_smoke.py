import subprocess
import os
import pytest
import sys
import time
import shutil
import pandas as pd
from datetime import datetime

@pytest.mark.parametrize("exchange, instrument", [("tinkoff", "SBER"), ("bybit", "BTCUSDT")])
def test_download_data_smoke(exchange, instrument, tmp_path):
    """
    Дымовой тест для download_data.py.
    Проверяет, что скрипт успешно запускается и создает файл в правильной
    структуре (data/exchange/interval/) для разных бирж.
    """
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

    expected_file = data_dir / exchange / "5min" / f"{instrument.upper()}.parquet"
    assert os.path.exists(expected_file), f"Файл {expected_file} не был создан"


# --- Дымовые тесты для основного приложения ---

@pytest.mark.parametrize("rm_type, fixture_name", [
    ("FIXED", "perfect_market_data_fixture"),
    ("ATR", "atr_test_data_fixture")
])
def test_run_smoke(rm_type, fixture_name, request, tmp_path):
    """Дымовой тест для run_backtest.py."""
    fixture_data = request.getfixturevalue(fixture_name)

    test_workdir = tmp_path / "workdir"
    shutil.copytree(fixture_data["data_root"], test_workdir / "data")

    required_files = ["run_backtest.py", "search_space.py", "single_run_analyzer.py"]
    for f in required_files: shutil.copy(f, test_workdir)
    for d in ["core", "utils", "strategies"]: shutil.copytree(d, test_workdir / d)

    test_strategy_code = """
from strategies.base_strategy import BaseStrategy
from core.event import MarketEvent, SignalEvent
import pandas as pd
class SmokeTestStrategy(BaseStrategy):
    candle_interval = "5min"
    required_indicators = []
    min_history_needed = 1
    def __init__(self, events_queue, instrument):
        super().__init__(events_queue, instrument)
        self.bar_index = -1; self.entry_sent = False
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame: return data
    def calculate_signals(self, event):
        self.bar_index += 1
        if not self.entry_sent and self.bar_index == 4: # <-- Входим на 4-й
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
            self.entry_sent = True
        elif self.entry_sent and self.bar_index == 7: # <-- ВЫХОДИМ НА 7-й (последней)
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
"""
    (test_workdir / "strategies" / "smoke_test_strategy.py").write_text(test_strategy_code, encoding='utf-8')

    if rm_type == "ATR":
        config_path = test_workdir / "search_space.py"
        with open(config_path, "r", encoding="utf-8") as f:
            config_content = f.read()
        config_content = config_content.replace('"ATR_PERIOD": 14', '"ATR_PERIOD": 3')
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(config_content)

    command = [
        sys.executable, "run_backtest.py",
        "--strategy", "smoke_test_strategy",
        "--exchange", fixture_data["exchange"],
        "--instrument", fixture_data["instrument"],
        "--interval", "5min",
        "--rm", rm_type
    ]
    # <-- Добавим вывод для отладки в случае будущих проблем
    result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=test_workdir)
    if result.returncode != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        assert result.returncode == 0, "Процесс бэктеста завершился с ошибкой"


    assert os.path.exists(test_workdir / "logs")
    assert os.path.exists(test_workdir / "reports")
    assert any(f.endswith('.jsonl') for f in os.listdir(test_workdir / "logs"))
    assert any(f.endswith('.png') for f in os.listdir(test_workdir / "reports"))


def test_batch_tester_smoke(tmp_path):  # <-- УБИРАЕМ ФИКСТУРУ ИЗ АРГУМЕНТОВ
    """Улучшенный дымовой тест для run_batch_backtest.py."""
    test_workdir = tmp_path / "workdir"
    test_workdir.mkdir()

    # --- СОЗДАЕМ ТЕСТОВОЕ ОКРУЖЕНИЕ С НУЛЯ ---
    data_dir = test_workdir / "data" / "tinkoff" / "5min"
    os.makedirs(data_dir)

    # 1. Создаем один файл с валидными данными
    valid_data = {
        'time': [datetime(2023, 1, 2, 10, i) for i in range(10)],
        'open': [100] * 10, 'high': [100] * 10, 'low': [100] * 10, 'close': [100] * 10, 'volume': [1000] * 10
    }
    pd.DataFrame(valid_data).to_parquet(data_dir / "VALID_DATA.parquet")

    # 2. Создаем второй, пустой файл
    (data_dir / "EMPTY_DATA.parquet").touch()

    # Копируем зависимости
    required_files = ["run_batch_backtest.py", "run_backtest.py", "search_space.py", "single_run_analyzer.py"]
    for f in required_files: shutil.copy(f, test_workdir)
    for d in ["core", "utils", "strategies"]: shutil.copytree(d, test_workdir / d)

    # Создаем временную тестовую стратегию
    test_strategy_code = """
from strategies.base_strategy import BaseStrategy
from core.event import MarketEvent, SignalEvent
class SmokeBatchStrategy(BaseStrategy):
    candle_interval = "5min"
    def __init__(self, events_queue, instrument):
        super().__init__(events_queue, instrument)
        self.bar_index = -1; self.entry_sent = False
    def prepare_data(self, data): return data
    def calculate_signals(self, event):
        self.bar_index += 1
        if not self.entry_sent and self.bar_index == 5:
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name)); self.entry_sent = True
        elif self.entry_sent and self.bar_index == 8:
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
"""
    (test_workdir / "strategies" / "smoke_batch_strategy.py").write_text(test_strategy_code, encoding='utf-8')

    command = [
        sys.executable, "run_batch_backtest.py",
        "--strategy", "smoke_batch_strategy",
        "--exchange", "tinkoff",  # Указываем явно
        "--interval", "5min"
    ]

    subprocess.run(command, capture_output=True, text=True, check=True, cwd=test_workdir)

    logs_dir = test_workdir / "logs"
    reports_dir = test_workdir / "reports"

    # Ожидаем РОВНО 1 файл, так как EMPTY_DATA.parquet не создаст лог сделок
    assert len([f for f in os.listdir(logs_dir) if f.endswith('_trades.jsonl')]) == 1
    assert len([f for f in os.listdir(reports_dir) if f.endswith('.png')]) == 1


def test_dashboard_smoke(tmp_path):
    """Дымовой тест для main.py: проверяет, что он запускается без ошибок."""
    test_workdir = tmp_path / "workdir"
    logs_dir = test_workdir / "logs"
    data_dir = test_workdir / "data"
    os.makedirs(logs_dir)
    os.makedirs(data_dir / "tinkoff" / "5min")

    fake_log_content = '{"entry_timestamp_utc": "2023-01-02T10:05:00+00:00", "exit_timestamp_utc": "2023-01-02T10:10:00+00:00", "strategy_name": "Test", "exchange": "tinkoff", "instrument": "FAKE", "direction": "BUY", "entry_price": 100, "exit_price": 101, "pnl": 100, "exit_reason": "Signal", "interval": "5min", "risk_manager": "FIXED"}\n'
    (logs_dir / "fake_log_trades.jsonl").write_text(fake_log_content, encoding='utf-8')

    fake_data_df = pd.DataFrame(
        {'time': [pd.Timestamp('2023-01-02 10:05:00')], 'open': [100], 'high': [100], 'low': [100], 'close': [100]})
    fake_data_df.to_parquet(data_dir / "tinkoff" / "5min" / "FAKE.parquet")

    required_files = ["main.py", "search_space.py", "single_run_analyzer.py", "comparative.py"]
    for f in required_files:
        if os.path.exists(f):
            shutil.copy(f, test_workdir)

    if os.path.exists("utils"):
        shutil.copytree("utils", test_workdir / "utils")

    command = [
        "streamlit", "run", "main.py",
        "--server.runOnSave=false",
        "--server.headless=true"
    ]

    process = subprocess.Popen(command, cwd=test_workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(5)

    return_code = process.poll()
    assert return_code is None, f"Процесс дашборда упал с ошибкой. stderr: {process.stderr.read()}"

    process.terminate()
    process.wait()