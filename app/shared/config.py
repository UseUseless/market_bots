"""
Модуль конфигурации приложения.

Этот модуль определяет класс `AppConfig`, который служит единым источником истины
для всех настроек проекта. Он использует библиотеку `pydantic-settings` для:
1. Валидации типов данных (например, чтобы капитал был числом).
2. Автоматического чтения переменных окружения из файла `.env`.
3. Предоставления дефолтных значений, если переменные не заданы.

Экземпляр `config` инициализируется в конце файла и импортируется в другие модули.
"""

from pathlib import Path
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.shared.primitives import ExchangeType


class AppConfig(BaseSettings):
    """
    Основной класс конфигурации.

    Наследуется от `BaseSettings`, что позволяет автоматически мапить
    переменные окружения (например, `TINKOFF_TOKEN`) в атрибуты класса.
    """

    # =========================================================================
    # 1. Основные пути (File System Paths)
    # =========================================================================

    # Определяем корень проекта относительно текущего файла.
    # app/shared/config.py -> app/shared -> app -> [ROOT]
    BASE_DIR: Path = Path(__file__).parent.parent.parent

    @property
    def DATA_DIR(self) -> Path:
        """Путь к папке с историческими данными (raw data)."""
        return self.BASE_DIR / "data"

    @property
    def DATALISTS_DIR(self) -> Path:
        """Путь к спискам инструментов (тикеры для скачивания)."""
        return self.BASE_DIR / "datalists"

    @property
    def LOGS_DIR(self) -> Path:
        """Путь к папке для хранения лог-файлов."""
        return self.BASE_DIR / "logs"

    @property
    def REPORTS_DIR(self) -> Path:
        """Путь для сохранения отчетов (Excel, PNG)."""
        return self.BASE_DIR / "reports"

    @property
    def DB_PATH(self) -> Path:
        """Полный путь к файлу базы данных SQLite."""
        return self.BASE_DIR / "storage" / "market_bots.db"

    @property
    def PATH_CONFIG(self) -> Dict[str, str]:
        """
        Возвращает словарь всех путей в строковом формате.
        Удобно для передачи в функции, которые не ожидают объекты Path.
        """
        return {
            "DATA_DIR": str(self.DATA_DIR),
            "DATALISTS_DIR": str(self.DATALISTS_DIR),
            "LOGS_DIR": str(self.LOGS_DIR),
            # Подпапки логов
            "LOGS_BACKTEST_DIR": str(self.LOGS_DIR / "backtests"),
            "LOGS_BATCH_TEST_DIR": str(self.LOGS_DIR / "batch_tests"),
            "LOGS_OPTIMIZATION_DIR": str(self.LOGS_DIR / "optimizations"),
            "LOGS_LIVE_DIR": str(self.LOGS_DIR / "live"),
            "REPORTS_DIR": str(self.REPORTS_DIR),
            # Подпапки отчетов
            "REPORTS_BACKTEST_DIR": str(self.REPORTS_DIR / "backtests"),
            "REPORTS_BATCH_TEST_DIR": str(self.REPORTS_DIR / "batch_tests"),
            "REPORTS_OPTIMIZATION_DIR": str(self.REPORTS_DIR / "optimizations"),
        }

    # =========================================================================
    # 2. API Токены и Секреты (Secrets)
    # Значения загружаются из .env файла. Если их нет — будет None.
    # =========================================================================

    TINKOFF_TOKEN_READONLY: Optional[str] = None
    TINKOFF_TOKEN_FULL_ACCESS: Optional[str] = None
    TINKOFF_TOKEN_SANDBOX: Optional[str] = None
    TINKOFF_ACCOUNT_ID: Optional[str] = None

    BYBIT_TESTNET_API_KEY: Optional[str] = None
    BYBIT_TESTNET_API_SECRET: Optional[str] = None

    # =========================================================================
    # 3. Настройки Загрузчика Данных (Data Loader Config)
    # =========================================================================

    DL_DAYS_TO_LOAD: int = 365  # Сколько дней истории качать по умолчанию
    DL_LIQUID_COUNT: int = 10  # Сколько топ-инструментов брать в список
    DATA_FILE_EXTENSION: str = ".parquet"  # Формат хранения данных

    @property
    def DATA_LOADER_CONFIG(self) -> Dict[str, int]:
        """Агрегированные настройки для модуля Data Manager."""
        return {
            "DAYS_TO_LOAD": self.DL_DAYS_TO_LOAD,
            "LIQUID_INSTRUMENTS_COUNT": self.DL_LIQUID_COUNT
        }

    # =========================================================================
    # 4. Настройки Live Торговли (Live Trading Config)
    # =========================================================================

    LIVE_RECONNECT_DELAY: int = 10  # Секунд ожидания перед реконнектом
    LIVE_HISTORY_BUFFER_MULT: int = 2  # Коэффициент запаса истории

    @property
    def LIVE_TRADING_CONFIG(self) -> Dict[str, int]:
        """Агрегированные настройки для Live Engine."""
        return {
            "LIVE_RECONNECT_DELAY_SECONDS": self.LIVE_RECONNECT_DELAY,
            "LIVE_HISTORY_BUFFER_MULTIPLIER": self.LIVE_HISTORY_BUFFER_MULT
        }

    # =========================================================================
    # 5. Настройки Бэктеста (Backtest Config)
    # Эти параметры используются по умолчанию, если пользователь не ввел иные.
    # =========================================================================

    bt_initial_capital: float = 100000.0
    bt_commission_rate: float = 0.0005  # 0.05%
    bt_max_exposure: float = 0.25  # Макс. доля капитала на 1 позицию (без учета плеч)
    bt_slippage_enabled: bool = True  # Включить симуляцию проскальзывания
    bt_slippage_impact: float = 0.1  # Коэффициент влияния объема на цену

    @property
    def BACKTEST_CONFIG(self) -> Dict[str, Any]:
        """Агрегированные настройки для движка бэктеста."""
        return {
            "INITIAL_CAPITAL": self.bt_initial_capital,
            "COMMISSION_RATE": self.bt_commission_rate,
            "MAX_POSITION_EXPOSURE": self.bt_max_exposure,
            "SLIPPAGE_CONFIG": {
                "ENABLED": self.bt_slippage_enabled,
                "IMPACT_COEFFICIENT": self.bt_slippage_impact
            }
        }

    # =========================================================================
    # 6. Спецификации Бирж (Exchange Specs)
    # Маппинг интервалов и специфические настройки (сессии, аннуализация).
    # =========================================================================

    @property
    def EXCHANGE_INTERVAL_MAPS(self) -> Dict[str, Dict[str, str]]:
        """
        Маппинг наших строковых интервалов ('1min', '1hour')
        в специфичные константы API конкретной биржи.
        """
        return {
            ExchangeType.TINKOFF: {
                "1min": "CANDLE_INTERVAL_1_MIN", "2min": "CANDLE_INTERVAL_2_MIN",
                "3min": "CANDLE_INTERVAL_3_MIN", "5min": "CANDLE_INTERVAL_5_MIN",
                "10min": "CANDLE_INTERVAL_10_MIN", "15min": "CANDLE_INTERVAL_15_MIN",
                "30min": "CANDLE_INTERVAL_30_MIN", "1hour": "CANDLE_INTERVAL_HOUR",
                "2hour": "CANDLE_INTERVAL_2_HOUR", "4hour": "CANDLE_INTERVAL_4_HOUR",
                "1day": "CANDLE_INTERVAL_DAY", "1week": "CANDLE_INTERVAL_WEEK",
                "1month": "CANDLE_INTERVAL_MONTH",
            },
            ExchangeType.BYBIT: {
                "1min": "1", "3min": "3", "5min": "5", "15min": "15", "30min": "30", "1hour": "60",
                "2hour": "120", "4hour": "240", "6hour": "360", "12hour": "720", "1day": "D",
                "1week": "W", "1month": "M",
            }
        }

    @property
    def EXCHANGE_SPECIFIC_CONFIG(self) -> Dict[str, Dict[str, Any]]:
        """
        Уникальные параметры для каждой биржи.

        Параметры:
            SHARPE_ANNUALIZATION_FACTOR: Кол-во торговых дней в году (Крипта=365, Акции=252).
            SESSION_START/END: Время начала и конца основной сессии (для фильтрации премаркета).
            DEFAULT_CLASS_CODE: Класс инструмента по умолчанию (для Tinkoff).
        """
        return {
            ExchangeType.TINKOFF: {
                "SHARPE_ANNUALIZATION_FACTOR": 252,
                "SESSION_START_UTC": "06:50",
                "SESSION_END_UTC": "15:30",
                "DEFAULT_CLASS_CODE": "TQBR",
            },
            ExchangeType.BYBIT: {
                "SHARPE_ANNUALIZATION_FACTOR": 365,
                "SESSION_START_UTC": None,  # Крипта торгуется 24/7
                "SESSION_END_UTC": None,
            }
        }

    # --- Pydantic Config ---
    model_config = SettingsConfigDict(
        env_file=".env",  # Имя файла с секретами
        env_file_encoding="utf-8",  # Кодировка файла
        extra="ignore"  # Игнорировать лишние переменные в .env, не выбрасывая ошибку
    )


# Создаем единственный экземпляр (Singleton pattern), который будет импортироваться везде.
config = AppConfig()