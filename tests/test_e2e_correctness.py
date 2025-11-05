import os
import pandas as pd
from queue import Queue
import pytest

from strategies.base_strategy import BaseStrategy
from core.event import MarketEvent, SignalEvent
from run_backtest import run_backtest, setup_logging
from utils.file_io import load_trades_from_file

# --- Тестовые стратегии, разработанные специально для E2E тестов ---

class OneShotLongStrategy(BaseStrategy):
    """Генерирует ОДИН сигнал на лонг на 5-й свече и больше ничего не делает."""
    candle_interval = "5min"
    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        self.bar_index = -1
        self.signal_sent = False
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame: return data
    def calculate_signals(self, event: MarketEvent):
        self.bar_index += 1
        if not self.signal_sent and self.bar_index == 5:
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
            self.signal_sent = True

class OneShotShortStrategy(BaseStrategy):
    """Генерирует ОДИН сигнал на шорт на 15-й свече и больше ничего не делает."""
    candle_interval = "5min"
    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        self.bar_index = -1
        self.signal_sent = False
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame: return data
    def calculate_signals(self, event: MarketEvent):
        self.bar_index += 1
        if not self.signal_sent and self.bar_index == 15:
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
            self.signal_sent = True

class OneShotShortWithSignalExitStrategy(BaseStrategy):
    """Генерирует ОДИН сигнал на шорт на 15-й свече и ОДИН сигнал на выход на 20-й."""
    candle_interval = "5min"
    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        self.bar_index = -1
        self.entry_sent = False
        self.exit_sent = False
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame: return data
    def calculate_signals(self, event: MarketEvent):
        self.bar_index += 1
        if not self.entry_sent and self.bar_index == 15:
            self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))
            self.entry_sent = True
        elif self.entry_sent and not self.exit_sent and self.bar_index == 20:
            self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
            self.exit_sent = True

class AtrDummyStrategy(BaseStrategy):
    """Входит в лонг на 5-й свече, выходит на 8-й."""
    candle_interval = "5min"
    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        self.bar_index = -1
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame: return data
    def calculate_signals(self, event: MarketEvent):
        self.bar_index += 1
        if self.bar_index == 4: self.events_queue.put(SignalEvent(self.instrument, "BUY", self.name))
        elif self.bar_index == 8: self.events_queue.put(SignalEvent(self.instrument, "SELL", self.name))

# --- Тесты ---
def test_system_accounting_long_trade_with_tp(perfect_market_data_fixture, tmp_path, monkeypatch):
    """Проверяет E2E расчеты для лонг-сделки с выходом по Take Profit."""
    exchange = perfect_market_data_fixture["exchange"]
    instrument = perfect_market_data_fixture["instrument"]
    data_root = perfect_market_data_fixture["data_root"]
    monkeypatch.setattr("run_backtest.PATH_CONFIG", {"DATA_DIR": str(data_root)})
    monkeypatch.setattr("utils.file_io.PATH_CONFIG", {"DATA_DIR": str(data_root)})
    trade_log_path = tmp_path / "test_trades.jsonl"
    setup_logging(tmp_path / "test_run.log")
    monkeypatch.setattr("core.risk_manager.RISK_CONFIG", {
        "DEFAULT_RISK_PERCENT_LONG": 2.0, "DEFAULT_RISK_PERCENT_SHORT": 2.0, "FIXED_TP_RATIO": 3.0
    })
    monkeypatch.setattr("core.portfolio.BACKTEST_CONFIG",
                        {"INITIAL_CAPITAL": 100000.0, "COMMISSION_RATE": 0.0005, "SLIPPAGE_CONFIG": {"ENABLED": False}})

    run_backtest(
        trade_log_path=str(trade_log_path), exchange=exchange, interval="5min",
        risk_manager_type="FIXED", strategy_class=OneShotLongStrategy, instrument=instrument
    )

    assert os.path.exists(trade_log_path)
    trades_df = load_trades_from_file(str(trade_log_path))
    assert len(trades_df) == 1
    assert trades_df.iloc[0]['pnl'] == pytest.approx(5897.0, abs=1)
    assert trades_df.iloc[0]['exit_reason'] == 'Take Profit'

def test_system_accounting_short_trade_with_tp(perfect_market_data_fixture, tmp_path, monkeypatch):
    """Проверяет E2E расчеты для шорт-сделки с выходом по Take Profit."""
    exchange = perfect_market_data_fixture["exchange"]
    instrument = perfect_market_data_fixture["instrument"]
    data_root = perfect_market_data_fixture["data_root"]
    monkeypatch.setattr("run_backtest.PATH_CONFIG", {"DATA_DIR": str(data_root)})
    monkeypatch.setattr("utils.file_io.PATH_CONFIG", {"DATA_DIR": str(data_root)})
    trade_log_path = tmp_path / "test_trades.jsonl"
    setup_logging(tmp_path / "test_run.log")
    monkeypatch.setattr("core.risk_manager.RISK_CONFIG", {
        "DEFAULT_RISK_PERCENT_LONG": 2.0, "DEFAULT_RISK_PERCENT_SHORT": 2.0, "FIXED_TP_RATIO": 1.0
    })
    monkeypatch.setattr("core.portfolio.BACKTEST_CONFIG",
                        {"INITIAL_CAPITAL": 100000.0, "COMMISSION_RATE": 0.0005, "SLIPPAGE_CONFIG": {"ENABLED": False}})

    run_backtest(
        trade_log_path=str(trade_log_path), exchange=exchange, interval="5min",
        risk_manager_type="FIXED", strategy_class=OneShotShortStrategy, instrument=instrument
    )

    assert os.path.exists(trade_log_path)
    trades_df = load_trades_from_file(str(trade_log_path))
    assert len(trades_df) == 1
    trade = trades_df.iloc[0]
    # Проверяем реальный результат, который виден в логах - выход по TP
    assert trade['pnl'] == pytest.approx(1900.24, abs=1)
    assert trade['exit_reason'] == 'Take Profit'

def test_system_with_atr_risk_manager(atr_test_data_fixture, tmp_path, monkeypatch):
    """Проверяет E2E работу с AtrRiskManager и выход по сигналу."""
    exchange = atr_test_data_fixture["exchange"]
    instrument = atr_test_data_fixture["instrument"]
    data_root = atr_test_data_fixture["data_root"]
    monkeypatch.setattr("run_backtest.PATH_CONFIG", {"DATA_DIR": str(data_root)})
    monkeypatch.setattr("utils.file_io.PATH_CONFIG", {"DATA_DIR": str(data_root)})
    trade_log_path = tmp_path / "test_trades.jsonl"
    setup_logging(tmp_path / "test_run.log")
    monkeypatch.setattr("core.risk_manager.RISK_CONFIG", {
        "DEFAULT_RISK_PERCENT_LONG": 1.0, "DEFAULT_RISK_PERCENT_SHORT": 1.0,
        "ATR_PERIOD": 3, "ATR_MULTIPLIER_SL": 2.0, "ATR_MULTIPLIER_TP": 10.0 # Увеличиваем TP, чтобы он не сработал
    })
    monkeypatch.setattr("core.portfolio.BACKTEST_CONFIG",
                        {"INITIAL_CAPITAL": 100000.0, "COMMISSION_RATE": 0.0, "SLIPPAGE_CONFIG": {"ENABLED": False}})

    run_backtest(
        trade_log_path=str(trade_log_path), exchange=exchange, interval="5min",
        risk_manager_type="ATR", strategy_class=AtrDummyStrategy, instrument=instrument
    )

    assert os.path.exists(trade_log_path)
    trades_df = load_trades_from_file(str(trade_log_path))
    assert len(trades_df) == 1
    assert trades_df.iloc[0]['exit_reason'] == 'Signal'