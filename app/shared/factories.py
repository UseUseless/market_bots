"""
Фабрики объектов.

Централизует логику создания сложных объектов конфигурации,
устраняя дублирование кода в раннерах, оркестраторах и движках оптимизации.
"""

from typing import Dict, Any, Optional

from app.shared.schemas import TradingConfig, RunModeType
from app.shared.config import config as app_config
from app.strategies import AVAILABLE_STRATEGIES


class ConfigFactory:
    """
    Фабрика для создания TradingConfig.
    """

    @staticmethod
    def create_trading_config(
        mode: RunModeType,
        exchange: str,
        instrument: str,
        interval: str,
        strategy_name: str,
        strategy_params_override: Optional[Dict[str, Any]] = None,
        risk_config_override: Optional[Dict[str, Any]] = None
    ) -> TradingConfig:
        """
        Собирает полный объект конфигурации, объединяя:
        1. Дефолтные параметры стратегии (из кода).
        2. Пользовательские параметры (из CLI/DB/Optuna).
        3. Глобальные настройки системы (комиссии, капитал).

        Args:
            mode: Режим запуска (BACKTEST/LIVE/OPTIMIZATION).
            exchange: Биржа.
            instrument: Тикер.
            interval: Таймфрейм.
            strategy_name: Имя класса стратегии.
            strategy_params_override: Параметры для переопределения дефолтов.
            risk_config_override: Настройки риск-менеджера.

        Returns:
            TradingConfig: Готовый DTO.
        """
        # 1. Получение класса стратегии и дефолтных параметров
        strategy_cls = AVAILABLE_STRATEGIES.get(strategy_name)
        if not strategy_cls:
            raise ValueError(f"Стратегия '{strategy_name}' не найдена в реестре.")

        final_strategy_params = strategy_cls.get_default_params()
        
        # 2. Слияние с пользовательскими параметрами
        if strategy_params_override:
            final_strategy_params.update(strategy_params_override)

        # 3. Подготовка риск-конфига (дефолт = FIXED)
        final_risk_config = {"type": "FIXED"}
        if risk_config_override:
            final_risk_config.update(risk_config_override)

        # 4. Сборка объекта с учетом глобальных настроек
        return TradingConfig(
            mode=mode,
            exchange=exchange,
            instrument=instrument,
            interval=interval,
            strategy_name=strategy_name,
            strategy_params=final_strategy_params,
            risk_config=final_risk_config,
            # Глобальные константы из config.py
            initial_capital=app_config.BACKTEST_CONFIG["INITIAL_CAPITAL"],
            commission_rate=app_config.BACKTEST_CONFIG["COMMISSION_RATE"],
            slippage_config=app_config.BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {})
        )