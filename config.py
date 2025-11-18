import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

# --- API Токены ---
TOKEN_READONLY = os.getenv("TINKOFF_TOKEN_READONLY")
TOKEN_FULL_ACCESS = os.getenv("TINKOFF_TOKEN_FULL_ACCESS")
TOKEN_SANDBOX = os.getenv("TINKOFF_TOKEN_SANDBOX")
ACCOUNT_ID = os.getenv("TINKOFF_ACCOUNT_ID")

BYBIT_TESTNET_API_KEY = os.getenv("BYBIT_TESTNET_API_KEY")
BYBIT_TESTNET_API_SECRET = os.getenv("BYBIT_TESTNET_API_SECRET")

# --- ОБЩИЕ НАСТРОЙКИ ФРЕЙМВОРКА ---

# Пути к директориям
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # Корень проекта
PATH_CONFIG = {
    "DATA_DIR": os.path.join(BASE_DIR, "data"),
    "DATALISTS_DIR": os.path.join(BASE_DIR, "datalists"),

    "LOGS_DIR": os.path.join(BASE_DIR, "logs"),
    "LOGS_BACKTEST_DIR": os.path.join(BASE_DIR, "logs", "backtests"),
    "LOGS_BATCH_TEST_DIR": os.path.join(BASE_DIR, "logs", "batch_tests"),
    "LOGS_OPTIMIZATION_DIR": os.path.join(BASE_DIR, "logs", "optimizations"),
    "LOGS_LIVE_DIR": os.path.join(BASE_DIR, "logs", "live"),

    "REPORTS_DIR": os.path.join(BASE_DIR, "reports"),
    "REPORTS_BACKTEST_DIR": os.path.join(BASE_DIR, "reports", "backtests"),
    "REPORTS_BATCH_TEST_DIR": os.path.join(BASE_DIR, "reports", "batch_tests"),
    "REPORTS_OPTIMIZATION_DIR": os.path.join(BASE_DIR, "reports", "optimizations"),
}

DATA_FILE_EXTENSION = ".parquet"

# Настройки для загрузчика данных (download_data.py)
DATA_LOADER_CONFIG = {
    # Количество дней истории, которое будет загружено по умолчанию,
    # если не указать флаг --days при запуске download_data.py.
    "DAYS_TO_LOAD": 365,
    "LIQUID_INSTRUMENTS_COUNT": 10
}

LIVE_TRADING_CONFIG = {
    # Задержка перед переподключением к стриму в секундах
    "LIVE_RECONNECT_DELAY_SECONDS": 10,
    # Множитель для размера исторического буфера в live-режиме
    "LIVE_HISTORY_BUFFER_MULTIPLIER": 2,
}

EXCHANGE_INTERVAL_MAPS = {
    "tinkoff": {
        "1min": "CANDLE_INTERVAL_1_MIN", "2min": "CANDLE_INTERVAL_2_MIN",
        "3min": "CANDLE_INTERVAL_3_MIN", "5min": "CANDLE_INTERVAL_5_MIN",
        "10min": "CANDLE_INTERVAL_10_MIN", "15min": "CANDLE_INTERVAL_15_MIN",
        "30min": "CANDLE_INTERVAL_30_MIN", "1hour": "CANDLE_INTERVAL_HOUR",
        "2hour": "CANDLE_INTERVAL_2_HOUR", "4hour": "CANDLE_INTERVAL_4_HOUR",
        "1day": "CANDLE_INTERVAL_DAY", "1week": "CANDLE_INTERVAL_WEEK",
        "1month": "CANDLE_INTERVAL_MONTH",
    },
    "bybit": {
        "1min": "1", "3min": "3", "5min": "5", "15min": "15", "30min": "30", "1hour": "60",
        "2hour": "120", "4hour": "240", "6hour": "360", "12hour": "720", "1day": "D",
        "1week": "W", "1month": "M",
    }
}

# Настройки для бэктестера (portfolio.py)
BACKTEST_CONFIG = {
    # Начальный капитал для симуляции. От этого значения зависит расчет размера позиций.
    "INITIAL_CAPITAL": 100000.0,
    # Размер комиссии брокера в долях. 0.0005 = 0.05%.
    # Учет комиссии критически важен для реалистичности бэктеста.
    "COMMISSION_RATE": 0.0005,
    # Максимальная доля капитала, которую можно вложить в ОДНУ позицию.
    # Это правило контроля риска концентрации. 0.25 означает, что ни одна
    # сделка не может превышать 25% от общего капитала портфеля на момент входа.
    "MAX_POSITION_EXPOSURE": 0.25,
    # Настройки симуляции проскальзывания (slippage).
    # Проскальзывание - разница между ожидаемой ценой сделки и реальной ценой исполнения.
    "SLIPPAGE_CONFIG": {
        # Глобальный "рубильник" для модели проскальзывания.
        # Позволяет быстро сравнивать результаты с/без учета проскальзывания.
        "ENABLED": True,
        # Коэффициент влияния на цену. Это эмпирический параметр для нашей модели.
        # Чем он больше, тем сильнее наша сделка "двигает" цену против нас.
        # 0.1 - это умеренное, консервативное значение.
        "IMPACT_COEFFICIENT": 0.1,
    },
}

EXCHANGE_SPECIFIC_CONFIG = {
    "tinkoff": {
        "SHARPE_ANNUALIZATION_FACTOR": 252,
        # Время основной сессии MOEX в UTC
        "SESSION_START_UTC": "06:50",
        "SESSION_END_UTC": "15:30",
        # Класс-код для поиска акций
        "DEFAULT_CLASS_CODE": "TQBR",
    },
    "bybit": {
        # Для крипты, торгующейся 24/7, коэффициент 365
        "SHARPE_ANNUALIZATION_FACTOR": 365,
        # None означает отсутствие фильтрации по сессии (торговля 24/7)
        "SESSION_START_UTC": None,
        "SESSION_END_UTC": None,
    }
}