import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

# --- API Токены ---
TOKEN_READONLY = os.getenv("TINKOFF_TOKEN_READONLY")
TOKEN_FULL_ACCESS = os.getenv("TINKOFF_TOKEN_FULL_ACCESS")
TOKEN_SANDBOX = os.getenv("TINKOFF_TOKEN_SANDBOX")
ACCOUNT_ID = os.getenv("TINKOFF_ACCOUNT_ID")

# --- ОБЩИЕ НАСТРОЙКИ ФРЕЙМВОРКА ---

# Пути к директориям
PATH_CONFIG = {
    "DATA_DIR": "data",       # Папка для хранения исторических данных
    "LOGS_DIR": "logs",       # Папка для хранения логов выполнения и сделок
    "REPORTS_DIR": "reports", # Папка для хранения графических отчетов
}

# Настройки для загрузчика данных (download_data.py)
DATA_LOADER_CONFIG = {
    "DAYS_TO_LOAD": 365,  # Сколько дней истории загружается по умолчанию
}

# Настройки для бэктестера (portfolio.py)
BACKTEST_CONFIG = {
    "INITIAL_CAPITAL": 100000.0,  # Начальный капитал
    "COMMISSION_RATE": 0.0005,  # Размер комиссии в долях (0.05%)
    # Настройки симуляции проскальзывания ---
    "SLIPPAGE_CONFIG": {
        "ENABLED": True,
        # Коэффициент влияния на цену. Чем он больше, тем сильнее проскальзывание.
        # Подбирается эмпирически. 0.1 - умеренное влияние.
        "IMPACT_COEFFICIENT": 0.1,
    },
}
# Настройки для управления риском и размером позиции
RISK_CONFIG = {
    # Процент риска от капитала по умолчанию
    "DEFAULT_RISK_PERCENT_LONG": 3,  # 1% для длинных позиций
    "DEFAULT_RISK_PERCENT_SHORT": 2,  # 0.8% для коротких позиций

    # Параметры для модели на основе ATR
    "ATR_PERIOD": 14,
    "ATR_MULTIPLIER_SL": 4.0, # Ставить стоп на расстоянии *значение* * ATR
    "ATR_MULTIPLIER_TP": 8.0, # Ставить тейк на расстоянии *значение* * ATR (sl:tp = 1:2)
}

# --- НАСТРОЙКИ КОНКРЕТНЫХ СТРАТЕГИЙ ---
# Свой собственный словарь с настройками для каждой стратегии.
STRATEGY_CONFIG = {
    "TripleFilterStrategy": {
        "candle_interval": "5min", # Рекомендуемый свечной интервал
        # Специфичные параметры для этой стратегии
        "ema_fast_period": 9,
        "ema_slow_period": 21,
        "ema_trend_period": 200,
        "volume_sma_period": 20,
    },
}