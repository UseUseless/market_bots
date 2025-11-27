from pathlib import Path
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.shared.primitives import ExchangeType


class AppConfig(BaseSettings):
    """
    Единый источник конфигурации.
    Читает переменные из .env, определяет пути и хранит константы.
    """

    # --- 1. Основные пути (Paths) ---
    # Корень проекта (app/shared/config.py -> ../../)
    BASE_DIR: Path = Path(__file__).parent.parent.parent

    @property
    def DATA_DIR(self) -> Path: return self.BASE_DIR / "data"

    @property
    def DATALISTS_DIR(self) -> Path: return self.BASE_DIR / "datalists"

    @property
    def LOGS_DIR(self) -> Path: return self.BASE_DIR / "logs"

    @property
    def REPORTS_DIR(self) -> Path: return self.BASE_DIR / "reports"

    @property
    def DB_PATH(self) -> Path: return self.BASE_DIR / "storage" / "market_bots.db"

    # Генерируем структуру PATH_CONFIG для совместимости со старым кодом
    @property
    def PATH_CONFIG(self) -> Dict[str, str]:
        return {
            "DATA_DIR": str(self.DATA_DIR),
            "DATALISTS_DIR": str(self.DATALISTS_DIR),
            "LOGS_DIR": str(self.LOGS_DIR),
            "LOGS_BACKTEST_DIR": str(self.LOGS_DIR / "backtests"),
            "LOGS_BATCH_TEST_DIR": str(self.LOGS_DIR / "batch_tests"),
            "LOGS_OPTIMIZATION_DIR": str(self.LOGS_DIR / "optimizations"),
            "LOGS_LIVE_DIR": str(self.LOGS_DIR / "live"),
            "REPORTS_DIR": str(self.REPORTS_DIR),
            "REPORTS_BACKTEST_DIR": str(self.REPORTS_DIR / "backtests"),
            "REPORTS_BATCH_TEST_DIR": str(self.REPORTS_DIR / "batch_tests"),
            "REPORTS_OPTIMIZATION_DIR": str(self.REPORTS_DIR / "optimizations"),
        }

    # --- 2. API Токены (Secrets) ---
    TINKOFF_TOKEN_READONLY: Optional[str] = None
    TINKOFF_TOKEN_FULL_ACCESS: Optional[str] = None
    TINKOFF_TOKEN_SANDBOX: Optional[str] = None
    TINKOFF_ACCOUNT_ID: Optional[str] = None

    BYBIT_TESTNET_API_KEY: Optional[str] = None
    BYBIT_TESTNET_API_SECRET: Optional[str] = None

    # --- 3. Data Loader Config ---
    DL_DAYS_TO_LOAD: int = 365
    DL_LIQUID_COUNT: int = 10
    DATA_FILE_EXTENSION: str = ".parquet"

    @property
    def DATA_LOADER_CONFIG(self) -> Dict[str, int]:
        return {
            "DAYS_TO_LOAD": self.DL_DAYS_TO_LOAD,
            "LIQUID_INSTRUMENTS_COUNT": self.DL_LIQUID_COUNT
        }

    # --- 4. Live Trading Config ---
    LIVE_RECONNECT_DELAY: int = 10
    LIVE_HISTORY_BUFFER_MULT: int = 2

    @property
    def LIVE_TRADING_CONFIG(self) -> Dict[str, int]:
        return {
            "LIVE_RECONNECT_DELAY_SECONDS": self.LIVE_RECONNECT_DELAY,
            "LIVE_HISTORY_BUFFER_MULTIPLIER": self.LIVE_HISTORY_BUFFER_MULT
        }

    # --- 5. Backtest Config ---
    bt_initial_capital: float = 100000.0
    bt_commission_rate: float = 0.0005
    bt_max_exposure: float = 0.25
    bt_slippage_enabled: bool = True
    bt_slippage_impact: float = 0.1

    @property
    def BACKTEST_CONFIG(self) -> Dict[str, Any]:
        return {
            "INITIAL_CAPITAL": self.bt_initial_capital,
            "COMMISSION_RATE": self.bt_commission_rate,
            "MAX_POSITION_EXPOSURE": self.bt_max_exposure,
            "SLIPPAGE_CONFIG": {
                "ENABLED": self.bt_slippage_enabled,
                "IMPACT_COEFFICIENT": self.bt_slippage_impact
            }
        }

    @property
    def EXCHANGE_INTERVAL_MAPS(self) -> Dict[str, Dict[str, str]]:
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
        return {
            ExchangeType.TINKOFF: {
                "SHARPE_ANNUALIZATION_FACTOR": 252,
                "SESSION_START_UTC": "06:50",
                "SESSION_END_UTC": "15:30",
                "DEFAULT_CLASS_CODE": "TQBR",
            },
            ExchangeType.BYBIT: {
                "SHARPE_ANNUALIZATION_FACTOR": 365,
                "SESSION_START_UTC": None,
                "SESSION_END_UTC": None,
            }
        }

    # --- Pydantic Config ---
    model_config = SettingsConfigDict(
        env_file=".env",            # 1. Откуда читать
        env_file_encoding="utf-8",  # 2. В какой кодировке
        extra="ignore"              # 3. Что делать с лишним
    )


# Инициализация объекта настроек
config = AppConfig()