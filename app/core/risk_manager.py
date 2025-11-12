from abc import ABC, abstractmethod
from typing import Literal, Dict, Any
from dataclasses import dataclass
import pandas as pd

# Тип для выбора модели риск-менеджера
RiskManagerType = Literal["FIXED", "ATR"]

@dataclass
class TradeRiskProfile:
    """
    Структура данных, которая инкапсулирует все параметры риска для одной сделки.
    Причина создания этого класса - уйти от передачи множества отдельных аргументов
    (sl, tp, risk_amount...) между функциями. Теперь мы передаем один понятный объект всегда.
    """
    stop_loss_price: float      # Абсолютная цена стоп-лосса
    take_profit_price: float    # Абсолютная цена тейк-профита
    risk_per_share: float       # Количество денег, которым рискуем на 1 акцию (abs(entry - stop))
    risk_amount: float          # Сумма денег, которой рискуем на сделке (процент от капитала)

class BaseRiskManager(ABC):
    """
    Абстрактный базовый класс для всех менеджеров риска.
    Его задача - создавать полный профиль риска для сделки (объект TradeRiskProfile).
    """

    # Процент риска от капитала по умолчанию. Это ключевой параметр для контроля убытков.
    # Например, 1% означает, что при срабатывании стоп-лосса мы потеряем не более 1% от текущего капитала.
    # Мы разделяем их для лонга и шорта, так как можем захотеть рисковать по-разному
    # в зависимости от направления рынка (например, быть более консервативными в шортах).
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
        self.params = params
        self.risk_percent_long = self.params["risk_percent_long"]
        self.risk_percent_short = self.params["risk_percent_short"]

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        """
        Извлекает параметры по умолчанию из params_config, включая родительские.
        """
        config = {}
        for base_class in reversed(cls.__mro__):
            if hasattr(base_class, 'params_config'):
                config.update(base_class.params_config)
        return {name: p_config['default'] for name, p_config in config.items()}

    @abstractmethod
    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float, last_candle: pd.Series) -> TradeRiskProfile:
        """
        Рассчитывает и возвращает полный объект TradeRiskProfile.

        Аргументы:
        - entry_price: предполагаемая цена входа в позицию.
        - direction: направление ('BUY' или 'SELL').
        - capital: текущий капитал для расчета общего риска.
        - last_candle: последняя свеча, может содержать нужные индикаторы (например, ATR).
        """
        raise NotImplementedError

class FixedRiskManager(BaseRiskManager):
    """
    Рассчитывает профиль риска на основе фиксированных процентов от капитала
    """

    params_config = {
        **BaseRiskManager.params_config,
        "tp_ratio": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 1.0, "high": 7.0, "step": 0.25,
            "description": "Соотношение Risk/Reward (TP/SL)."
        }
    }

    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        # Параметры теперь берутся из переданного словаря
        self.tp_ratio = self.params["tp_ratio"]

    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float, last_candle: pd.Series) -> TradeRiskProfile:
        risk_percent = self.risk_percent_long if direction == 'BUY' else self.risk_percent_short
        sl_percent = risk_percent / 100.0

        if direction == 'BUY':
            stop_loss_price = entry_price * (1 - sl_percent)
            take_profit_price = entry_price * (1 + (sl_percent * self.tp_ratio))
        else:
            stop_loss_price = entry_price * (1 + sl_percent)
            take_profit_price = entry_price * (1 - (sl_percent * self.tp_ratio))

        risk_per_share = abs(entry_price - stop_loss_price)
        risk_amount = capital * sl_percent

        if take_profit_price < 0:
            take_profit_price = 0.0001

        return TradeRiskProfile(
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount
        )

class AtrRiskManager(BaseRiskManager):
    """
    Рассчитывает профиль риска на основе волатильности (ATR).
    """
    params_config = {
        **BaseRiskManager.params_config,
        "atr_period": {
            "type": "int", "default": 14, "optimizable": False,
            "description": "Период для расчета ATR."
        },
        "atr_multiplier_sl": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 1.0, "high": 4.0, "step": 0.25,
            "description": "Множитель ATR для стоп-лосса."
        },
        "atr_multiplier_tp": {
            "type": "float", "default": 4.0, "optimizable": True,
            "low": 2.0, "high": 8.0, "step": 0.5,
            "description": "Множитель ATR для тейк-профита."
        }
    }

    def __init__(self, params: Dict[str, Any]):
        super().__init__(params)
        self.sl_multiplier = self.params["atr_multiplier_sl"]
        self.tp_multiplier = self.params["atr_multiplier_tp"]
        self.atr_period = self.params["atr_period"]

    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float, last_candle: pd.Series) -> TradeRiskProfile:
        risk_percent = self.risk_percent_long if direction == 'BUY' else self.risk_percent_short
        sl_percent = risk_percent / 100.0
        # Извлекаем значение ATR из данных последней свечи.
        # Имя колонки (f'ATR_{self.atr_period}') должно совпадать с тем, что генерирует FeatureEngine.
        atr_value = last_candle.get(f'ATR_{self.atr_period}')
        if not atr_value or atr_value <= 0:
            # Валидация: если ATR не рассчитан (например, в начале истории) или равен нулю,
            # мы не можем рассчитать риск, поэтому выбрасываем исключение, чтобы сигнал был проигнорирован.
            raise ValueError("ATR value is invalid, cannot calculate risk profile.")

        # Рассчитываем "расстояние до стопа" в денежном выражении.
        sl_distance = atr_value * self.sl_multiplier
        # Применяем это расстояние для расчета абсолютного уровня стоп-лосса.
        stop_loss_price = entry_price - sl_distance if direction == 'BUY' else entry_price + sl_distance

        # Рассчитываем "расстояние до тейка" в денежном выражении.
        # (По сути если в config соотношение ATR_MULTIPLIER sl и tp сделать как 1:2,
        # то будет то же самое tp_ratio = 2)
        tp_distance = atr_value * self.tp_multiplier
        # Применяем это расстояние для расчета абсолютного уровня стоп-лосса.
        take_profit_price = entry_price + tp_distance if direction == 'BUY' else entry_price - tp_distance

        # Риск на одну акцию здесь - это расстояние до стопа, основанное на ATR.
        risk_per_share = abs(entry_price - stop_loss_price)
        # Важный момент: общий денежный риск (risk_amount) мы по-прежнему считаем
        # как процент от капитала. Это позволяет контролировать общий риск портфеля,
        # даже если стопы ставятся на основе волатильности.
        risk_percent = self.risk_percent_long if direction == 'BUY' else self.risk_percent_short
        risk_amount = capital * (risk_percent / 100.0)

        return TradeRiskProfile(
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount
        )

AVAILABLE_RISK_MANAGERS = {
    "FIXED": FixedRiskManager,
    "ATR": AtrRiskManager,
}

# Пояснение расчетов в целом (для фикса. для atr идет расчет на основе коэфф волатильности последних свеч)
# Представим, что у нас есть: Капитал: 100 000 ₽:                 (capital)
# Правило риска: Рисковать не более 2% капитала на одну сделку.   (risk_percent)
# Цена акции: 200 ₽                                               (entry_price)
# Наш максимальный допустимый убыток в деньгах на эту сделку составляет:
# 100 000 ₽ * 2% = 2000 ₽.
# Сначала я должен понять, где мой сигнал на вход становится недействительным. Это и будет мой стоп-лосс.
# Только после этого я пойму, сколько акций я могу себе позволить купить".
#
# Цена входа: 200 ₽                                               (entry_price)
# Цена стоп-лосса: 190 ₽   (допустим)                             (stop_loss_price) - за этот расчет отвечает другой "менеджер"
# Риск на одну акцию = 200₽-190₽=10₽. (200 ₽-210₽=-10₽.)          (risk_per_share)  - Это цена нашей "ошибки" за каждую купленную акцию.
# А иначе мы бы могли взять весь капитал, разделить все на акции,посчитать наш стоп лосс в 2% и получилось бы,
# что мы выходим из сделки не по 190, а по 196, что рано бы выбивало.
# Чтобы избежать выбивания приходилось бы занижать стоп-лосс,
# то есть рсиковали бы гораздо большими деньгами на самом деле.
#
# Расчет размера позиции (сколько можно купить): Теперь мы используем нашу константу (максимальный убыток 1000 ₽ - risk_amount) и делим ее на риск на одну акцию.
# Размер позиции = (Максимальный допустимый убыток) / (Риск на одну акцию)
# Размер позиции = 2000 ₽ / 10 ₽/акцию = 200 акций.               (1) - считается в sizer.py
# Результат: Бот покупает 200 акций. Если сработает стоп-лосс, его убыток составит: 200 акций * 10 ₽ = 2000 ₽, что в точности равно 2% от капитала. Риск полностью под контролем.