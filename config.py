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
PATH_CONFIG = {
    "DATA_DIR": "data",       # Папка для хранения исторических данных в формате .parquet.
    "LOGS_DIR": "logs",       # Папка для хранения логов выполнения (.log) и сделок (.csv).
    "REPORTS_DIR": "reports", # Папка для хранения графических отчетов анализа (.png).
}

DATA_FILE_EXTENSION = ".parquet"

# Настройки для загрузчика данных (download_data.py)
DATA_LOADER_CONFIG = {
    # Количество дней истории, которое будет загружено по умолчанию,
    # если не указать флаг --days при запуске download_data.py.
    "DAYS_TO_LOAD": 365,
    "LIQUID_INSTRUMENTS_COUNT": 20
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
# Настройки для управления риском и размером позиции
RISK_CONFIG = {
    # Процент риска от капитала по умолчанию. Это ключевой параметр для контроля убытков.
    # Например, 1% означает, что при срабатывании стоп-лосса мы потеряем не более 1% от текущего капитала.
    # Мы разделяем их для лонга и шорта, так как можем захотеть рисковать по-разному
    # в зависимости от направления рынка (например, быть более консервативными в шортах).
    "DEFAULT_RISK_PERCENT_LONG": 3.0,
    "DEFAULT_RISK_PERCENT_SHORT": 5.0,

    # Коэффициент для расчета тейк-профита в FixedRiskManager.
    # Это значение определяет соотношение риска к прибыли (Risk/Reward Ratio).
    # Например, значение 2.0 означает, что потенциальная прибыль (расстояние до тейк-профита)
    # в 2 раза больше потенциального убытка (расстояния до стоп-лосса).
    # Таким образом, мы получаем R/R Ratio = 1:2.
    # Если у стратегии соотношение риска к прибыли 2:1, ей достаточно быть правой всего в 34% случаев, чтобы быть безубыточной (в теории, без учета комиссий).
    # Любой процент выигрышных сделок (Win Rate) выше этого значения будет приносить прибыль.
    # Если бы tp_ratio был 1.0 (соотношение 1:1), стратегии нужно было бы выигрывать более 50% сделок, чтобы быть прибыльной.
    "FIXED_TP_RATIO": 2.0,

    # Параметры для модели управления риском на основе ATR (AtrRiskManager).
    "ATR_PERIOD": 14,           # Стандартный период для расчета индикатора ATR.
    "ATR_MULTIPLIER_SL": 4.0,   # Множитель для стоп-лосса. SL = цена_входа - (ATR * этот_множитель).
                                # Чем больше значение, тем дальше (безопаснее, но и дороже) стоп.
    "ATR_MULTIPLIER_TP": 8.0,   # Множитель для тейк-профита. TP = цена_входа + (ATR * этот_множитель).
                                # Соотношение TP/SL здесь 4.0/2.0 = 2, что дает нам Risk/Reward Ratio 1:2.
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
    "VolatilityBreakoutStrategy": {
        "candle_interval": "1hour",
        "variant": "ADX_Donchian",

        # --- СЕКЦИЯ УПРАВЛЕНИЯ ВХОДОМ ---
        "entry_logic": {
            # Сколько свечей после "выстрела" мы готовы ждать пробоя канала.
            "breakout_timeout_bars": 3,

            # Использовать ли свечу подтверждения после пробоя.
            "confirm_breakout": False,

            # Ждать ли отката (pullback) к EMA после подтвержденного пробоя.
            # Работает, только если confirm_breakout = True.
            "wait_for_pullback": False,

            # Период EMA, к которой мы ждем откат.
            "pullback_ema_period": 8,

            # Таймаут: сколько свечей мы готовы ждать отката, прежде чем отменить сигнал.
            "pullback_timeout_bars": 5
        },

        "exit_logic": {
            "use_trailing_stop": False,
            "atr_ts_period": 14,
            "atr_ts_multiplier": 3.0
        },

        "ClassicSqueeze_params": {
            "bb_len": 20, "bb_std": 2.0, "kc_len": 20,
            "kc_atr_multiplier": 1.5, "trend_ema_period": 50
        },
        "ADX_Donchian_params": {
            "bb_len": 20, "bb_std": 2.0, "squeeze_period": 50,
            "squeeze_quantile": 0.05, "donchian_len": 20,
            "adx_len": 14, "adx_threshold": 20
        }
    },
    "MeanReversionStrategy": {
        "candle_interval": "15min",  # Рекомендуемый интервал. Хорошо работает на младших и средних таймфреймах.

        # Период для расчета скользящей средней и стандартного отклонения.
        # Классическое значение - 20.
        "sma_period": 20,

        # Пороги Z-Score для входа в позицию.
        # Значения 2.0 и -2.0 соответствуют примерно 95% доверительному интервалу.
        "z_score_upper_threshold": 2.0,
        "z_score_lower_threshold": -2.0
    },
}