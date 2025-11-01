from abc import ABC, abstractmethod
from typing import Literal
from dataclasses import dataclass
import pandas as pd
from config import RISK_CONFIG

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

    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float,
                               last_candle: pd.Series) -> TradeRiskProfile:

        # На каком проценте от входной стоимости ставим стоп-лосс (например на вход число 3)
        risk_percent = RISK_CONFIG["DEFAULT_RISK_PERCENT_LONG"] if direction == 'BUY' else RISK_CONFIG["DEFAULT_RISK_PERCENT_SHORT"]

        # Перевод процент во float 3-> 0.03
        sl_percent = risk_percent / 100.0
        # Рассчитываем абсолютный уровень стоп-лосса. Ex:Buy:100*(1-0.03)=97
        # Для покупки (BUY) он ниже цены входа, для продажи (SELL) - выше.
        stop_loss_price = entry_price * (1 - sl_percent) if direction == 'BUY' else entry_price * (1 + sl_percent)

        # Рассчитываем, сколько мы теряем на одной акции, если сработает стоп. Ex:100-97=3
        risk_per_share = abs(entry_price - stop_loss_price)
        # Получаем соотношение риска к прибыли из нашего конфига.
        tp_ratio = RISK_CONFIG["FIXED_TP_RATIO"]
        # Рассчитываем тейк-профит на основе соотношения риска к прибыли (tp_ratio). Ex:100+(3*2)=106
        take_profit_price = entry_price + (risk_per_share * tp_ratio) if direction == 'BUY' else entry_price - (
                    risk_per_share * tp_ratio)

        # Рассчитываем общую сумму, которой мы готовы рискнуть в этой сделке.
        # Потом посчитаем сколько акций получится купить
        risk_amount = capital * sl_percent

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
    def __init__(self):
        # Загружает параметры из конфига. Не зависит от конкретной свечи, поэтому мы можем создать один экземпляр
        self.sl_multiplier: float = RISK_CONFIG["ATR_MULTIPLIER_SL"]
        self.tp_multiplier: float = RISK_CONFIG["ATR_MULTIPLIER_TP"]
        self.atr_period: int = RISK_CONFIG["ATR_PERIOD"]

    def calculate_risk_profile(self, entry_price: float, direction: str, capital: float, last_candle: pd.Series) -> TradeRiskProfile:
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
        risk_percent = RISK_CONFIG["DEFAULT_RISK_PERCENT_LONG"] if direction == 'BUY' else RISK_CONFIG["DEFAULT_RISK_PERCENT_SHORT"]
        risk_amount = capital * (risk_percent / 100.0)

        return TradeRiskProfile(
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount
        )

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