import os
import pandas as pd
from queue import Queue
import pytest

# --- Импорты компонентов нашего фреймворка ---

# Импортируем базовый класс для создания нашей тестовой стратегии
from strategies.base_strategy import BaseStrategy
# Импортируем типы событий, которые будет генерировать наша стратегия
from core.event import MarketEvent, SignalEvent
# Импортируем "дирижера" бэктеста и настройщик логов
from run_backtest import run_backtest, setup_logging
# Импортируем функцию для чтения результатов (логов сделок)
from utils.file_io import load_trades_from_file
from core.data_handler import HistoricLocalDataHandler


# --- Шаг 1: Создаем простую и предсказуемую стратегию специально для этого теста ---

class DummyStrategy(BaseStrategy):
    """
    Эта стратегия создана исключительно для теста. Ее поведение полностью
    детерминировано: она выдает сигнал на ПОКУПКУ на 5-й свече и сигнал
    на ПРОДАЖУ (для открытия шорта) на 15-й. Это позволяет нам точно
    знать, когда и какие сделки должны произойти.
    """
    candle_interval = "5min"

    def __init__(self, events_queue: Queue, instrument: str):
        super().__init__(events_queue, instrument)
        # Счетчик свечей, чтобы знать, когда подавать сигнал
        self.bar_index = -1

    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        # Для этого теста нам не нужны никакие индикаторы, просто возвращаем данные как есть.
        return data

    def calculate_signals(self, event: MarketEvent):
        self.bar_index += 1
        # Генерируем сигнал на покупку на 5-й свече (индекс 5)
        if self.bar_index == 5:
            self.events_queue.put(SignalEvent(instrument=self.instrument, direction="BUY", strategy_id=self.name))
        # Генерируем сигнал на продажу на 15-й свече (индекс 15)
        elif self.bar_index == 15:
            self.events_queue.put(SignalEvent(instrument=self.instrument, direction="SELL", strategy_id=self.name))


# --- Шаг 2: Основная функция теста ---

def test_system_accounting_correctness(perfect_market_data_fixture, tmp_path, monkeypatch):
    """
    Это end-to-end тест, который проверяет всю систему в сборе на заранее
    подготовленных, "идеальных" данных. Он сверяет итоговый PnL каждой
    сделки с эталонным значением, рассчитанным вручную.
    """
    # --- ЭТАП 1: ПОДГОТОВКА (Arrange) ---

    # Получаем информацию о тестовых данных из фикстуры (conftest.py)
    # Фикстура теперь возвращает словарь с путем к данным, биржей и тикером
    exchange = perfect_market_data_fixture["exchange"]
    instrument_ticker = perfect_market_data_fixture["instrument"]
    data_root = perfect_market_data_fixture["data_root"]

    # Теперь мы можем подменить PATH_CONFIG в том модуле, который его читает - run_backtest.py
    monkeypatch.setattr("run_backtest.PATH_CONFIG", {"DATA_DIR": str(data_root)})

    # Также подменим его для file_io, так как он тоже его использует
    monkeypatch.setattr("utils.file_io.PATH_CONFIG", {"DATA_DIR": str(data_root)})

    # Готовим пути для логов во временной папке, которую предоставляет pytest
    logs_dir = tmp_path / "logs"
    os.makedirs(logs_dir, exist_ok=True)
    trade_log_path = os.path.join(logs_dir, "test_trades.jsonl")
    run_log_path = os.path.join(logs_dir, "test_run.log")

    # Настраиваем логирование, чтобы в случае ошибки можно было посмотреть детальный лог
    setup_logging(run_log_path)

    # "Подменяем" конфиги риска и портфеля на время теста.
    # Это гарантирует, что наши ручные расчеты будут совпадать с расчетами
    # программы, даже если кто-то изменит основной файл config.py.
    monkeypatch.setattr("core.risk_manager.RISK_CONFIG", {
        "DEFAULT_RISK_PERCENT_LONG": 2.0,
        "DEFAULT_RISK_PERCENT_SHORT": 2.0,
        "FIXED_TP_RATIO": 3.0
    })
    monkeypatch.setattr("core.portfolio.BACKTEST_CONFIG", {
        "INITIAL_CAPITAL": 100000.0,
        "COMMISSION_RATE": 0.0005,
        "SLIPPAGE_CONFIG": {"ENABLED": False}  # Отключаем проскальзывание для точности расчетов
    })

    # --- ЭТАП 2: ДЕЙСТВИЕ (Act) ---

    # Запускаем полный цикл бэктеста, вызывая нашу основную функцию-дирижер
    run_backtest(
        trade_log_path=trade_log_path,
        exchange=exchange,
        interval="5min",
        risk_manager_type="FIXED",
        strategy_class=DummyStrategy,
        instrument=instrument_ticker
    )

    # --- ЭТАП 3: ПРОВЕРКА (Assert) ---

    # 1. Убеждаемся, что файл с результатами сделок вообще был создан
    assert os.path.exists(trade_log_path), "Лог-файл со сделками не был создан!"

    # 2. Загружаем результаты
    trades_df = load_trades_from_file(trade_log_path)

    # 3. Проверяем, что было совершено ровно 2 сделки, как и было задумано
    assert len(trades_df) == 2, f"Ожидалось 2 сделки, но было совершено {len(trades_df)}"

    # 4. Детально проверяем каждую сделку

    # --- Проверка Сделки №1 (Лонг, закрытие по Take Profit) ---
    trade1 = trades_df.iloc[0]
    # Ручной расчет для проверки:
    # Капитал: 100000. Риск: 2% (2000). Вход: 100. SL по правилу 2% = 98.
    # Риск на акцию = 100 - 98 = 2. TP Ratio = 3.0, значит TP = 100 + (2 * 3.0) = 106.
    # Размер позиции = 2000 / 2 = 1000 лотов.
    # Выход по TP на цене 106. PnL = (106 - 100) * 1000 = 6000.
    # Комиссия = (100*1000 + 106*1000) * 0.0005 = 103.
    # Итоговый PnL = 6000 - 103 = 5897.
    assert trade1['pnl'] == pytest.approx(5897.0, abs=1)

    # --- Проверка Сделки №2 (Шорт, закрытие по Stop Loss) ---
    trade2 = trades_df.iloc[1]
    # Ручной расчет для проверки:
    # Новый капитал ~ 105897. Риск: 2% (2117.94). Вход: 120. SL по правилу 2% = 122.4.
    # Риск на акцию = 122.4 - 120 = 2.4.
    # Размер позиции = 2117.94 / 2.4 = 882.475 -> 882 лота (округление вниз).
    # Выход по SL на цене 122.4. PnL = (120 - 122.4) * 882 = -2116.8.
    # Комиссия = (120*882 + 122.4*882) * 0.0005 = 106.9.
    # Итоговый PnL = -2116.8 - 106.9 = -2223.7.
    assert trade2['pnl'] == pytest.approx(-2223.7, abs=1)