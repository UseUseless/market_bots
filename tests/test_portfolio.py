import pandas as pd
import pytest
from queue import Queue
from datetime import datetime, timezone

from app.core import Portfolio
from app.core import FillEvent
from app.strategies.base_strategy import BaseStrategy


# --- Вспомогательные классы-заглушки ---
class MockStrategy(BaseStrategy):
    """Минимальная заглушка для инициализации Portfolio."""
    candle_interval = "5min"

    def prepare_data(self, data): pass

    def calculate_signals(self, event): pass


# --- Фикстура для подготовки экземпляра Portfolio ---
@pytest.fixture
def portfolio_instance(tmp_path, monkeypatch) -> Portfolio:
    """Создает готовый к работе экземпляр Portfolio для тестов."""
    # Подменяем конфиги, чтобы тест был изолированным
    monkeypatch.setattr("core.portfolio.BACKTEST_CONFIG", {
        "INITIAL_CAPITAL": 100000.0,
        "COMMISSION_RATE": 0.001,  # 0.1% для простоты расчетов
        "SLIPPAGE_CONFIG": {"ENABLED": False}  # Отключаем проскальзывание
    })
    monkeypatch.setattr("core.risk_manager.RISK_CONFIG", {
        "DEFAULT_RISK_PERCENT_LONG": 2.0,
        "DEFAULT_RISK_PERCENT_SHORT": 2.0,
        "FIXED_TP_RATIO": 2.0
    })

    # Создаем экземпляр Portfolio
    portfolio = Portfolio(
        events_queue=Queue(),
        trade_log_file=str(tmp_path / "test_trades.jsonl"),
        strategy=MockStrategy(Queue(), "TEST_INSTR"),
        exchange="test_exchange",
        initial_capital=100000.0,
        commission_rate=0.001,
        interval="5min",
        risk_manager_type="FIXED",
        instrument_info={"lot_size": 1, "qty_step": 1.0, "min_order_qty": 1.0}
    )
    return portfolio


# --- Тест жизненного цикла сделки ---
def test_trade_lifecycle(portfolio_instance: Portfolio):
    """
    Проверяет полный жизненный цикл одной сделки: открытие, обновление состояния, закрытие.
    """
    portfolio = portfolio_instance
    instrument = "TEST_INSTR"

    # --- 1. ЭТАП ОТКРЫТИЯ СДЕЛКИ ---

    # Симулируем MarketEvent, который предшествует сделке
    # Он нужен, чтобы в portfolio.last_market_data появилась цена для расчетов
    open_candle_data = pd.Series({
        'time': datetime(2023, 1, 2, 10, 5, tzinfo=timezone.utc),
        'open': 100.0, 'high': 102.0, 'low': 99.0, 'close': 101.0, 'volume': 1000
    })
    portfolio.last_market_data[instrument] = open_candle_data

    # Симулируем FillEvent, как будто брокер исполнил наш ордер на покупку
    fill_open_event = FillEvent(
        timestamp=datetime.now(timezone.utc),
        instrument=instrument,
        quantity=50,
        direction="BUY",
        price=0,  # Не используется в бэктесте, цена берется из last_market_data
        commission=0
    )

    # Вызываем обработчик on_fill
    portfolio.on_fill(fill_open_event)

    # ПРОВЕРЯЕМ СОСТОЯНИЕ ПОСЛЕ ОТКРЫТИЯ
    assert instrument in portfolio.current_positions
    position = portfolio.current_positions[instrument]

    # Цена входа должна быть равна цене open свечи
    assert position['entry_price'] == 100.0
    assert position['quantity'] == 50
    # Проверяем расчет SL/TP (риск 2% -> SL=98, TP=104, т.к. TP_RATIO=2.0)
    assert position['stop_loss'] == pytest.approx(98.0)
    assert position['take_profit'] == pytest.approx(104.0)

    # --- 2. ЭТАП ЗАКРЫТИЯ СДЕЛКИ ---

    # Симулируем следующую свечу, на которой закроется сделка
    close_candle_data = pd.Series({
        'time': datetime(2023, 1, 2, 10, 10, tzinfo=timezone.utc),
        'open': 105.0, 'high': 106.0, 'low': 104.0, 'close': 105.0, 'volume': 1000
    })
    portfolio.last_market_data[instrument] = close_candle_data

    # Симулируем FillEvent на закрытие
    fill_close_event = FillEvent(
        timestamp=datetime.now(timezone.utc),
        instrument=instrument,
        quantity=50,
        direction="SELL",
        price=0,
        commission=0
    )

    # Вызываем обработчик on_fill
    portfolio.on_fill(fill_close_event)

    # ПРОВЕРЯЕМ СОСТОЯНИЕ ПОСЛЕ ЗАКРЫТИЯ
    assert instrument not in portfolio.current_positions  # Позиция должна быть закрыта
    assert len(portfolio.closed_trades) == 1  # Должна появиться запись о закрытой сделке

    # Проверяем расчет PnL и капитала
    # Цена входа = 100, цена выхода = 105 (open следующей свечи)
    # PnL = (105 - 100) * 50 = 250
    # Комиссия = (100 * 50 + 105 * 50) * 0.001 = 10.25
    # Итоговый PnL = 250 - 10.25 = 239.75
    # Новый капитал = 100000 + 239.75 = 100239.75
    assert portfolio.closed_trades[0]['pnl'] == pytest.approx(239.75)
    assert portfolio.current_capital == pytest.approx(100239.75)