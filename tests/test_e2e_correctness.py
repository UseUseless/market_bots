import os
import glob
import pandas as pd
from queue import Queue

import pytest
from analyzer import BacktestAnalyzer
from strategies.base_strategy import BaseStrategy
from core.event import MarketEvent, SignalEvent

# --- Импортируем НЕОБХОДИМЫЕ функции и классы из нашего приложения ---
# Мы импортируем "дирижера", а не весь скрипт
from run import run_backtest, setup_logging


# --- Шаг 1: Определяем тестовую стратегию (как и раньше) ---
class DummyStrategy(BaseStrategy):
    candle_interval = "5min"

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        self.bar_index = -1

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        return data

    def calculate_signals(self, event: MarketEvent):
        self.bar_index += 1
        if self.bar_index == 5:
            self.events_queue.put(SignalEvent(instrument=self.instrument, direction="BUY", strategy_id=self.name))
        elif self.bar_index == 15:
            self.events_queue.put(SignalEvent(instrument=self.instrument, direction="SELL", strategy_id=self.name))


# --- Шаг 2: Пишем тест, который вызывает run_backtest НАПРЯМУЮ ---
def test_system_accounting_correctness(perfect_market_data_fixture, tmp_path, monkeypatch):
    """
    Проверяет всю систему в сборе, вызывая run_backtest напрямую.
    Сверяет итоговый PnL с заранее рассчитанным эталонным значением.
    """
    # 1. Подготовка (Arrange)
    instrument_ticker = perfect_market_data_fixture

    # Готовим пути для логов во временной папке
    logs_dir = tmp_path / "logs"
    os.makedirs(logs_dir, exist_ok=True)
    trade_log_path = os.path.join(logs_dir, "test_trades.jsonl")
    run_log_path = os.path.join(logs_dir, "test_run.log")

    # Настраиваем логирование для теста
    setup_logging(run_log_path, backtest_mode=True)

    # Подменяем конфиги
    monkeypatch.setattr("core.risk_manager.RISK_CONFIG", {
        "DEFAULT_RISK_PERCENT_LONG": 2.0,
        "DEFAULT_RISK_PERCENT_SHORT": 2.0,
        "FIXED_TP_RATIO": 3.0
    })
    # Устанавливаем комиссию для точности расчетов
    monkeypatch.setattr("core.portfolio.BACKTEST_CONFIG", {
        "INITIAL_CAPITAL": 100000.0,
        "COMMISSION_RATE": 0.0005,
        "SLIPPAGE_CONFIG": {"ENABLED": False}  # Отключаем проскальзывание
    })

    # 2. Действие (Act)
    # Вызываем нашу основную функцию-дирижер НАПРЯМУЮ
    run_backtest(
        trade_log_path=trade_log_path,
        interval="5min",
        risk_manager_type="FIXED",
        strategy_class=DummyStrategy,  # Передаем наш тестовый класс
        instrument=instrument_ticker
    )

    # 3. Проверка (Assert)
    assert os.path.exists(trade_log_path), "Лог-файл со сделками не был создан!"

    trades_df = BacktestAnalyzer.load_trades_from_file(trade_log_path)

    assert len(trades_df) == 2, f"Ожидалось 2 сделки, но было совершено {len(trades_df)}"

    # --- Проверка Сделки №1 (Лонг, Take Profit) ---
    trade1 = trades_df.iloc[0]
    # Ручной расчет (без проскальзывания):
    # Вход: 100. Капитал: 100000. Риск: 2%. SL=98, TP=106.
    # risk_amount=2000. risk_per_share=2. quantity=1000.
    # Выход по TP=106. PnL = (106-100)*1000 = 6000.
    # Комиссия = (100*1000 + 106*1000)*0.0005 = 103.
    # Итоговый PnL = 6000 - 103 = 5897.
    assert trade1['pnl'] == pytest.approx(5897.0, abs=1)

    # --- Проверка Сделки №2 (Шорт, Stop Loss) ---
    trade2 = trades_df.iloc[1]
    # Ручной расчет:
    # Капитал ~ 105897. Вход: 120. Риск: 2%. SL=122.4.
    # risk_amount = 105897 * 0.02 = 2117.94. risk_per_share = 2.4. quantity = int(2117.94/2.4) = 882.
    # Выход по SL=122.4. PnL = (120-122.4)*882 = -2116.8.
    # Комиссия = (120*882 + 122.4*882)*0.0005 = 106.92.
    # Итоговый PnL = -2116.8 - 106.92 = -2223.72.
    assert trade2['pnl'] == pytest.approx(-2223.72, abs=1)