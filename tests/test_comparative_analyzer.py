import pandas as pd
import pytest
from app.services.analytics import ComparativeAnalyzer


# Используем pytest.fixture для создания тестовых данных.
# Это позволяет переиспользовать их в нескольких тестах.
@pytest.fixture
def sample_portfolio_trades() -> pd.DataFrame:
    """
    Создает DataFrame, имитирующий отсортированный список сделок из нескольких бэктестов,
    с уже рассчитанной портфельной кривой капитала.
    """
    initial_capital = 200000.0  # Симулируем портфель из 2-х инструментов
    trades = {
        'pnl': [1000, -500, 2000, -800],
    }
    df = pd.DataFrame(trades)
    df['cumulative_pnl'] = df['pnl'].cumsum()
    df['portfolio_equity'] = initial_capital + df['cumulative_pnl']

    # Результаты после каждой сделки:
    # 1. 201000
    # 2. 200500
    # 3. 202500
    # 4. 201700

    return df


def test_calculate_portfolio_metrics(sample_portfolio_trades):
    """
    Проверяет корректность расчета агрегированных портфельных метрик.
    """
    # 1. ПОДГОТОВКА (Arrange)
    # Создаем фейковый summary_df, он нужен только для инициализации класса
    mock_summary_df = pd.DataFrame([{"File": "dummy.jsonl"}])
    analyzer = ComparativeAnalyzer(mock_summary_df)

    initial_capital = 200000.0
    trades_df = sample_portfolio_trades

    # 2. ДЕЙСТВИЕ (Act)
    # Вызываем наш приватный метод, который мы хотим протестировать
    metrics = analyzer._calculate_portfolio_metrics(trades_df, initial_capital)

    # 3. ПРОВЕРКА (Assert)
    # Сверяем каждый результат с эталонным, рассчитанным вручную.

    # Общий PnL = 1000 - 500 + 2000 - 800 = 1700
    # PnL, % = (1700 / 200000) * 100 = 0.85%
    assert metrics['PnL, %'] == pytest.approx(0.85)

    # 2 прибыльные из 4 сделок = 50%
    assert metrics['Win Rate, %'] == pytest.approx(50.0)

    # Gross Profit = 1000 + 2000 = 3000
    # Gross Loss = 500 + 800 = 1300
    # Profit Factor = 3000 / 1300 = 2.307
    assert metrics['Profit Factor'] == pytest.approx(2.307, abs=0.01)

    # Max Drawdown:
    # Equity: 200000 (start), 201000, 200500, 202500, 201700
    # HWM:    200000,         201000, 201000, 202500, 202500
    # Drawdown $: 0,          0,      -500,   0,      -800
    # Drawdown %: 0,          0,      -0.24%, 0,      -0.39%
    # Максимальная просадка = 0.39%
    assert metrics['Max Drawdown, %'] == pytest.approx(0.395, abs=0.01)

    assert metrics['Total Trades'] == 4


def test_calculate_portfolio_metrics_empty_df():
    """
    Проверяет, что метод корректно обрабатывает пустой DataFrame.
    """
    mock_summary_df = pd.DataFrame([{"File": "dummy.jsonl"}])
    analyzer = ComparativeAnalyzer(mock_summary_df)

    metrics = analyzer._calculate_portfolio_metrics(pd.DataFrame(), 200000.0)

    # Ожидаем, что метод вернет пустую Series
    assert isinstance(metrics, pd.Series)
    assert metrics.empty