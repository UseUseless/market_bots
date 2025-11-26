import pandas as pd
import pytest
from types import SimpleNamespace

from app.core.risk_engine.risk_manager import FixedRiskManager, AtrRiskManager
from app.core.risk_engine.sizer import FixedRiskSizer
from app.services.analytics import SingleRunAnalyzer

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

def test_fixed_risk_manager_short(monkeypatch):
    """Проверяет, что FixedRiskManager правильно рассчитывает SL/TP для шорта."""
    monkeypatch.setattr(
        "core.risk_manager.RISK_CONFIG",
        {"DEFAULT_RISK_PERCENT_SHORT": 5.0, "FIXED_TP_RATIO": 2.0}
    )
    manager = FixedRiskManager()
    profile = manager.calculate_risk_profile(
        entry_price=200.0, direction='SELL', capital=10000.0, last_candle=pd.Series()
    )
    # SL должен быть ВЫШЕ цены входа для шорта
    assert profile.stop_loss_price == pytest.approx(210.0)
    # TP должен быть НИЖЕ цены входа для шорта
    assert profile.take_profit_price == pytest.approx(180.0)
    assert profile.risk_amount == pytest.approx(500.0)

def test_atr_risk_manager_long(monkeypatch):
    """Проверяет, что AtrRiskManager правильно рассчитывает SL/TP для лонга."""
    monkeypatch.setattr(
        "core.risk_manager.RISK_CONFIG",
        {
            "DEFAULT_RISK_PERCENT_LONG": 1.0, "ATR_PERIOD": 14,
            "ATR_MULTIPLIER_SL": 1.5, "ATR_MULTIPLIER_TP": 3.0
        }
    )
    manager = AtrRiskManager()
    mock_candle = pd.Series({'ATR_14': 10.0})
    profile = manager.calculate_risk_profile(
        entry_price=150.0, direction='BUY', capital=20000.0, last_candle=mock_candle
    )
    assert profile.stop_loss_price == pytest.approx(135.0) # 150 - 10 * 1.5
    assert profile.take_profit_price == pytest.approx(180.0) # 150 + 10 * 3.0
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
def portfolio_fixture(monkeypatch) -> "Portfolio":
    """Фикстура для создания экземпляра Portfolio для модульных тестов."""
    monkeypatch.setattr("config.BACKTEST_CONFIG", {
        "INITIAL_CAPITAL": 100000.0, "COMMISSION_RATE": 0.0,
        "SLIPPAGE_CONFIG": {"ENABLED": True, "IMPACT_COEFFICIENT": 0.1}
    })
    from app.core import Portfolio
    return Portfolio(
        events_queue=MockQueue(), trade_log_file="", strategy=MockStrategy(),
        exchange="tinkoff", initial_capital=100000, commission_rate=0,
        interval="5min", risk_manager_type="FIXED", instrument_info={}
    )

@pytest.mark.parametrize("quantity_float, rules, expected", [
    # Тест 1: Акции с лотностью 10 (Сбер)
    (25.0, {"lot_size": 10, "qty_step": 1.0, "min_order_qty": 10.0}, 20),
    # Тест 2: Криптовалюта с шагом 0.01
    (1.2345, {"lot_size": 1, "qty_step": 0.01, "min_order_qty": 0.01}, 1.23),
    # Тест 3: Срабатывание минимального размера ордера
    (0.8, {"lot_size": 1, "qty_step": 0.1, "min_order_qty": 1.0}, 0),
    # Тест 4: Количество меньше лота
    (8.0, {"lot_size": 10, "qty_step": 1.0, "min_order_qty": 10.0}, 0),
    # Тест 5: Дробное количество с целым шагом
    (15.7, {"lot_size": 1, "qty_step": 1.0, "min_order_qty": 1.0}, 15),
])
def test_adjust_quantity_for_rules(portfolio_fixture, quantity_float, rules, expected):
    """Проверяет корректность корректировки размера позиции по правилам биржи."""
    portfolio = portfolio_fixture
    # Подменяем instrument_info для теста
    portfolio.instrument_info = rules
    adjusted_qty = portfolio._adjust_quantity_for_rules(quantity_float)
    assert adjusted_qty == expected


def test_slippage_increases_buy_price(portfolio_fixture):
    """Тест: проскальзывание должно УВЕЛИЧИВАТЬ цену при покупке (BUY)."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_fixture
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


def test_slippage_decreases_sell_price(portfolio_fixture):
    """Тест: проскальзывание должно УМЕНЬШАТЬ цену при продаже (SELL)."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_fixture
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


def test_slippage_disabled(portfolio_fixture):
    """Тест: если проскальзывание отключено, цена не должна меняться."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_fixture
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


def test_slippage_zero_volume(portfolio_fixture):
    """Тест: если объем на свече равен нулю, цена не должна меняться."""
    # 1. Подготовка (Arrange)
    portfolio = portfolio_fixture
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

class TestBacktestAnalyzer:
    @pytest.fixture
    def sample_trades(self):
        """Создает DataFrame с набором тестовых сделок."""
        trades = {
            'pnl': [1000, -500, 1500, -200],
        }
        df = pd.DataFrame(trades)
        # Добавляем фейковые исторические данные для Buy&Hold
        hist_data = pd.DataFrame({
            'open': [100] * 10,
            'close': [110] * 10,
        })
        return df, hist_data

    def test_calculate_metrics_happy_path(self, sample_trades):
        """Проверяет корректность расчета основных метрик."""
        trades_df, hist_data = sample_trades
        analyzer = SingleRunAnalyzer(trades_df, hist_data, 100000.0, "5min", "FIXED")
        metrics = analyzer.calculate_metrics()

        assert float(metrics["Total PnL (Strategy)"].split(' ')[0]) == pytest.approx(1800.0)
        assert float(metrics["Win Rate"].replace('%', '')) == pytest.approx(50.0)

        # ВОЗВРАЩАЕМ ПРАВИЛЬНОЕ ЗНАЧЕНИЕ: 2500 / 700 = 3.5714...
        assert float(metrics["Profit Factor"]) == pytest.approx(3.57, abs=0.01)

        # Max Drawdown = 500 / 101000 = 0.495...%
        assert float(metrics["Max Drawdown"].replace('%', '')) == pytest.approx(0.50, abs=0.01)

        assert int(metrics["Total Trades"]) == 4

    def test_calculate_metrics_no_losses(self, sample_trades):
        """Проверяет, что Profit Factor равен 'inf' при отсутствии убытков."""
        trades_df, hist_data = sample_trades
        trades_df = trades_df[trades_df['pnl'] > 0] # Оставляем только прибыльные
        analyzer = SingleRunAnalyzer(trades_df, hist_data, 100000.0, "5min", "FIXED")
        metrics = analyzer.calculate_metrics()
        assert metrics["Profit Factor"] == "inf"

    def test_analyzer_raises_on_empty_df(self):
        """Проверяет, что анализатор падает, если ему передать пустой DataFrame."""
        with pytest.raises(ValueError, match="DataFrame со сделками не может быть пустым."):
            SingleRunAnalyzer(pd.DataFrame(), pd.DataFrame(), 100000.0, "5min", "FIXED")