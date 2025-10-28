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
    "DAYS_TO_LOAD": 730,  # Сколько дней истории загружать
}

# Настройки для бэктестера (portfolio.py)
BACKTEST_CONFIG = {
    "INITIAL_CAPITAL": 100000.0,  # Начальный капитал
    "COMMISSION_RATE": 0.0005,  # Размер комиссии в долях (0.05%)
}

# --- 3. НАСТРОЙКИ КОНКРЕТНЫХ СТРАТЕГИЙ ---
# Свой собственный словарь с настройками для каждой стратегии.

STRATEGY_CONFIG = {
    "TripleFilterStrategy": {
        "candle_interval": "5min",
        "stop_loss_percent": 0.7,
        "take_profit_percent": 1.4,
        # Специфичные параметры для этой стратегии
        "ema_fast_period": 9,
        "ema_slow_period": 21,
        "ema_trend_period": 200,
        "volume_sma_period": 20,
    },
}