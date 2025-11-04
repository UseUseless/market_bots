import pandas as pd
import pytest
from types import SimpleNamespace

from core.risk_manager import FixedRiskManager, AtrRiskManager
from core.sizer import FixedRiskSizer

# --- Тесты для RiskManager ---

def test_fixed_risk_manager_long(monkeypatch):
    """Проверяет, что FixedRiskManager правильно рассчитывает SL/TP для лонга."""
    # Подготовка (Arrange)
    # Указываем полный путь к объекту, который мы хотим подменить
    monkeypatch.setattr(
        "core.risk_manager.RISK_CONFIG",
        {
            "DEFAULT_RISK_PERCENT_LONG": 2.0,
            "FIXED_TP_RATIO": 3.0
        }
    )
    manager = FixedRiskManager()

    # Действие (Act)
    profile = manager.calculate_risk_profile(
        entry_price=100.0,
        direction='BUY',
        capital=10000.0,
        last_candle=pd.Series()
    )

    # Проверка (Assert)
    assert profile.stop_loss_price == pytest.approx(98.0)
    assert profile.take_profit_price == pytest.approx(106.0)
    assert profile.risk_amount == pytest.approx(200.0)


def test_atr_risk_manager_short(monkeypatch):
    """Проверяет, что AtrRiskManager правильно рассчитывает SL/TP для шорта."""
    # Подготовка (Arrange)
    # Указываем полный путь
    monkeypatch.setattr(
        "core.risk_manager.RISK_CONFIG",
        {
            "DEFAULT_RISK_PERCENT_SHORT": 1.5,
            "ATR_PERIOD": 14,
            "ATR_MULTIPLIER_SL": 2.0,
            "ATR_MULTIPLIER_TP": 4.0
        }
    )
    manager = AtrRiskManager()
    mock_candle = pd.Series({'ATR_14': 5.0})

    # Действие (Act)
    profile = manager.calculate_risk_profile(
        entry_price=200.0,
        direction='SELL',
        capital=50000.0,
        last_candle=mock_candle
    )

    # Проверка (Assert)
    assert profile.stop_loss_price == pytest.approx(210.0)
    assert profile.take_profit_price == pytest.approx(180.0)
    assert profile.risk_amount == pytest.approx(750.0)


# --- Тесты для Sizer ---

def test_fixed_risk_sizer():
    """Проверяет, что FixedRiskSizer правильно рассчитывает размер позиции."""
    # 1. Подготовка (Arrange)
    sizer = FixedRiskSizer()
    # Создаем фейковый профиль риска
    mock_profile = SimpleNamespace(risk_amount=100.0, risk_per_share=2.0)

    # 2. Действие (Act)
    quantity = sizer.calculate_size(mock_profile)

    # 3. Проверка (Assert)
    assert quantity == pytest.approx(50.0)  # 100 / 2


def test_fixed_risk_sizer_zero_risk():
    """Проверяет, что Sizer возвращает 0, если риск на акцию равен нулю."""
    # 1. Подготовка (Arrange)
    sizer = FixedRiskSizer()
    mock_profile = SimpleNamespace(risk_amount=100.0, risk_per_share=0.0)

    # 2. Действие (Act)
    quantity = sizer.calculate_size(mock_profile)

    # 3. Проверка (Assert)
    assert quantity == 0.0


def test_atr_risk_manager_invalid_atr():
    """Проверяет, что AtrRiskManager падает, если ATR некорректен."""
    # 1. Подготовка (Arrange)
    manager = AtrRiskManager()
    # Свеча с нулевым ATR
    mock_candle_zero = pd.Series({'ATR_14': 0.0})

    # 2. Действие и 3. Проверка (Act & Assert)
    # Мы ожидаем, что код вызовет исключение ValueError
    with pytest.raises(ValueError, match="ATR value is invalid"):
        manager.calculate_risk_profile(
            entry_price=100.0, direction='BUY', capital=10000.0, last_candle=mock_candle_zero
        )

    # Свеча без нужной колонки ATR
    mock_candle_missing = pd.Series({'some_other_col': 10})
    with pytest.raises(ValueError, match="ATR value is invalid"):
        manager.calculate_risk_profile(
            entry_price=100.0, direction='BUY', capital=10000.0, last_candle=mock_candle_missing
        )

# --- Тесты для Portfolio._simulate_slippage ---

class MockQueue:
    def put(self, item): pass

class MockStrategy:
    pass


@pytest.fixture
def portfolio_for_slippage_test(monkeypatch) -> "Portfolio":
    """Фикстура для создания экземпляра Portfolio для тестов проскальзывания."""
    monkeypatch.setattr("config.BACKTEST_CONFIG", {
        "INITIAL_CAPITAL": 100000.0,
        "COMMISSION_RATE": 0.0,
        "SLIPPAGE_CONFIG": {
            "ENABLED": True,
            "IMPACT_COEFFICIENT": 0.1,
        }
    })
    # Импортируем Portfolio прямо здесь, чтобы избежать циклических зависимостей на верхнем уровне
    from core.portfolio import Portfolio

    # Создаем экземпляр с минимально необходимыми "заглушками"
    return Portfolio(
        events_queue=MockQueue(),
        trade_log_file="",
        strategy=MockStrategy(),
        exchange="tinkoff",
        initial_capital=100000,
        commission_rate=0,
        interval="5min",
        risk_manager_type="FIXED",
        instrument_info={}
    )


def test_slippage_increases_buy_price(portfolio_for_slippage_test):
    """Тест: проскальзывание должно УВЕЛИЧИВАТЬ цену при покупке (BUY)."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_for_slippage_test
    ideal_price = 100.0

    # 2. Действие (Act)
    execution_price = portfolio._simulate_slippage(
        ideal_price=ideal_price,
        quantity=100,
        direction='BUY',
        candle_volume=1000
    )

    # 3. Проверка (Assert)
    assert execution_price > ideal_price


def test_slippage_decreases_sell_price(portfolio_for_slippage_test):
    """Тест: проскальзывание должно УМЕНЬШАТЬ цену при продаже (SELL)."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_for_slippage_test
    ideal_price = 100.0

    # 2. Действие (Act)
    execution_price = portfolio._simulate_slippage(
        ideal_price=ideal_price,
        quantity=100,
        direction='SELL',
        candle_volume=1000
    )

    # 3. Проверка (Assert)
    assert execution_price < ideal_price


def test_slippage_disabled(portfolio_for_slippage_test):
    """Тест: если проскальзывание отключено, цена не должна меняться."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_for_slippage_test
    portfolio.slippage_enabled = False  # Отключаем вручную
    ideal_price = 100.0

    # 2. Действие (Act)
    execution_price = portfolio._simulate_slippage(
        ideal_price=ideal_price,
        quantity=100,
        direction='BUY',
        candle_volume=1000
    )

    # 3. Проверка (Assert)
    assert execution_price == ideal_price


def test_slippage_zero_volume(portfolio_for_slippage_test):
    """Тест: если объем на свече равен нулю, цена не должна меняться."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_for_slippage_test
    ideal_price = 100.0

    # 2. Действие (Act)
    execution_price = portfolio._simulate_slippage(
        ideal_price=ideal_price,
        quantity=100,
        direction='BUY',
        candle_volume=0  # Нулевой объем
    )

    # 3. Проверка (Assert)
    assert execution_price == ideal_price