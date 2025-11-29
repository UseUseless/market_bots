"""
Модуль управления рисками (Risk Management).

Отвечает за расчет параметров защиты сделки (Stop Loss, Take Profit) и определение
допустимого денежного риска. Риск-менеджер — это "тормоз" системы, который не дает
стратегии слить депозит.

Основные задачи:
1.  Определить цену выхода в убыток (Stop Loss).
2.  Определить цену выхода в прибыль (Take Profit).
3.  Рассчитать сумму денег, которой мы готовы рискнуть в этой сделке.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import pandas as pd

from app.shared.primitives import TradeDirection, TradeRiskProfile


class BaseRiskManager(ABC):
    """
    Абстрактный базовый класс для всех менеджеров риска.

    Определяет общий интерфейс и базовые параметры (процент риска на сделку).
    Наследники должны реализовать метод `calculate_risk_profile`.

    Attributes:
        params_config (Dict): Метаданные параметров для UI и оптимизатора.
    """

    params_config: Dict[str, Dict[str, Any]] = {
        "risk_percent_long": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 0.5, "high": 5.0, "step": 0.1,
            "description": "Процент риска от капитала для лонг позиций."
        },
        "risk_percent_short": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 0.5, "high": 5.0, "step": 0.1,
            "description": "Процент риска от капитала для шорт позиций."
        }
    }

    def __init__(self, params: Dict[str, Any]):
        """
        Инициализирует риск-менеджер параметрами из конфига.

        Args:
            params (Dict[str, Any]): Словарь с настройками (например, risk_percent_long).
        """
        self.params = params
        self.risk_percent_long = self.params["risk_percent_long"]
        self.risk_percent_short = self.params["risk_percent_short"]

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        """
        Собирает дефолтные параметры из конфигурации класса.

        Returns:
            Dict[str, Any]: Словарь значений по умолчанию.
        """
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    @abstractmethod
    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float,
                               last_candle: Optional[pd.Series]) -> TradeRiskProfile:
        """
        Рассчитывает полный профиль риска для потенциальной сделки.

        Args:
            entry_price (float): Планируемая цена входа.
            direction (str): Направление торговли (BUY/SELL).
            capital (float): Текущий доступный капитал (Equity).
            last_candle (Optional[pd.Series]): Данные последней свечи (нужны для ATR и волатильности).

        Returns:
            TradeRiskProfile: Объект с рассчитанными уровнями SL/TP и денежным риском.
        """
        raise NotImplementedError


class FixedRiskManager(BaseRiskManager):
    """
    Риск-менеджмент с фиксированным процентом Stop Loss.

    Алгоритм:
    1.  Стоп-лосс ставится на фиксированном % от цены входа (например, 2% ниже входа).
    2.  Тейк-профит рассчитывается как N * StopLoss (Risk/Reward Ratio).

    Пример:
        Вход: 100 руб. Stop Loss (2%): 98 руб. Риск на акцию: 2 руб.
        При капитале 100,000 руб и риске 1% (1000 руб),
        мы можем купить 1000 / 2 = 500 акций.
    """

    params_config = {
        **BaseRiskManager.params_config,
        "tp_ratio": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 1.0, "high": 4.0, "step": 0.25,
            "description": "Соотношение Risk/Reward (TP/SL). Если 2.0, то TP в 2 раза больше SL."
        }
    }

    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.tp_ratio = self.params["tp_ratio"]

    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float,
                               last_candle: Optional[pd.Series]) -> TradeRiskProfile:
        """
        Рассчитывает SL/TP как процент от цены входа.
        """
        if entry_price <= 0:
            raise ValueError(f"Цена входа должна быть положительной, получено: {entry_price}")

        risk_percent = self.risk_percent_long if direction == TradeDirection.BUY else self.risk_percent_short
        sl_percent = risk_percent / 100.0

        if direction == TradeDirection.BUY:
            stop_loss_price = entry_price * (1 - sl_percent)
            take_profit_price = entry_price * (1 + (sl_percent * self.tp_ratio))
        else:
            stop_loss_price = entry_price * (1 + sl_percent)
            take_profit_price = entry_price * (1 - (sl_percent * self.tp_ratio))

        # Защита от отрицательных цен для TP
        if take_profit_price <= 0:
            take_profit_price = 0.0001

        risk_per_share = abs(entry_price - stop_loss_price)
        risk_amount = capital * sl_percent

        return TradeRiskProfile(
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount
        )


class AtrRiskManager(BaseRiskManager):
    """
    Адаптивный риск-менеджмент на основе волатильности (ATR).

    Учитывает текущую "шумность" рынка.
    Алгоритм:
    1.  Берет значение индикатора ATR (Average True Range) из последней свечи.
    2.  Стоп-лосс ставится на расстоянии `Multiplier * ATR` от входа.
    3.  Тейк-профит ставится на расстоянии `Multiplier * ATR` от входа.

    Преимущество:
        На спокойном рынке стопы короткие (можно взять позицию больше).
        На бурном рынке стопы широкие (чтобы не выбило шумом).
    """

    params_config = {
        **BaseRiskManager.params_config,
        "atr_period": {
            "type": "int", "default": 14, "optimizable": False,
            "description": "Период индикатора ATR."
        },
        "atr_multiplier_sl": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 1.0, "high": 4.0, "step": 0.25,
            "description": "Множитель ATR для дистанции Стоп-лосса."
        },
        "atr_multiplier_tp": {
            "type": "float", "default": 4.0, "optimizable": True,
            "low": 2.0, "high": 8.0, "step": 0.5,
            "description": "Множитель ATR для дистанции Тейк-профита."
        }
    }

    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.sl_multiplier = self.params["atr_multiplier_sl"]
        self.tp_multiplier = self.params["atr_multiplier_tp"]
        self.atr_period = self.params["atr_period"]

    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float,
                               last_candle: Optional[pd.Series]) -> TradeRiskProfile:
        """
        Рассчитывает SL/TP на основе значения ATR.
        """
        if entry_price <= 0:
            raise ValueError(f"Цена входа должна быть положительной, получено: {entry_price}")

        risk_percent = self.risk_percent_long if direction == TradeDirection.BUY else self.risk_percent_short

        if last_candle is None:
            raise ValueError("Для AtrRiskManager необходимы данные последней свечи (last_candle).")

        # Получаем значение ATR из свечи (оно должно быть посчитано FeatureEngine)
        atr_value = last_candle.get(f'ATR_{self.atr_period}')

        if atr_value is None or atr_value <= 1e-9:
            raise ValueError(f"Некорректное значение ATR ({atr_value}). Проверьте FeatureEngine.")

        sl_distance = atr_value * self.sl_multiplier
        tp_distance = atr_value * self.tp_multiplier

        if direction == TradeDirection.BUY:
            stop_loss_price = entry_price - sl_distance
            take_profit_price = entry_price + tp_distance
        else:
            stop_loss_price = entry_price + sl_distance
            take_profit_price = entry_price - tp_distance

        if take_profit_price <= 0:
            take_profit_price = 0.0001

        risk_per_share = abs(entry_price - stop_loss_price)
        risk_amount = capital * (risk_percent / 100.0)

        return TradeRiskProfile(
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount
        )


# Реестр доступных менеджеров для фабричного создания
AVAILABLE_RISK_MANAGERS = {
    "FIXED": FixedRiskManager,
    "ATR": AtrRiskManager,
}