"""
Модуль управления рисками (Risk Management).

Объединяет в себе логику определения Stop Loss, Take Profit
и расчет объема позиции на основе допустимого денежного риска.

Классы:
    - RiskManager: Калькулятор параметров сделки.
"""

from typing import Optional, Tuple, Dict, Any
import pandas as pd

from app.shared.schemas import TradingConfig
from app.shared.types import TradeDirection, TradeRiskProfile

# Список доступных режимов для UI/CLI
RISK_MANAGEMENT_TYPES = ["FIXED", "ATR"]


class RiskManager:
    """
    Универсальный менеджер рисков.

    Отвечает за расчет параметров сделки перед входом.
    Поддерживает два режима работы (определяется в `config.risk_config['type']`):
    1. **FIXED**: Стоп-лосс как фиксированный процент от цены входа.
    2. **ATR**: Стоп-лосс на основе волатильности (индикатор ATR).

    Также содержит метаданные (`params_config`) для автоматической оптимизации
    параметров через Optuna.

    Attributes:
        config (TradingConfig): Глобальная конфигурация сессии.
        risk_cfg (Dict): Подмножество настроек, относящихся к рискам.
        risk_type (str): Текущий режим ('FIXED' или 'ATR').
    """

    # Конфигурация для оптимизатора (диапазоны перебора гиперпараметров)
    params_config = {
        # --- Параметры для FIXED режима ---
        "stop_loss_pct": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 1.0, "high": 5.0, "step": 0.1,
            "description": "Stop Loss в % от цены входа (для Fixed mode)."
        },
        "take_profit_ratio": {
            "type": "float", "default": 1.5, "optimizable": True,
            "low": 1.0, "high": 5.0, "step": 0.5,
            "description": "Отношение TP к SL (Risk/Reward Ratio)."
        },
        # --- Параметры для ATR режима ---
        "atr_period": {
            "type": "int", "default": 14, "optimizable": False,
            "description": "Период индикатора ATR."
        },
        "atr_mult_sl": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 1.0, "high": 4.0, "step": 0.25,
            "description": "Множитель ATR для Stop Loss."
        },
        "atr_mult_tp": {
            "type": "float", "default": 4.0, "optimizable": True,
            "low": 2.0, "high": 8.0, "step": 0.5,
            "description": "Множитель ATR для Take Profit."
        },
        # --- Общие параметры ---
        "risk_per_trade_pct": {
            "type": "float", "default": 1.0, "optimizable": True,
            "low": 0.5, "high": 5.0, "step": 0.1,
            "description": "Риск на сделку (% от текущего капитала)."
        }
    }

    def __init__(self, config: TradingConfig):
        """
        Инициализирует менеджер рисков.

        Args:
            config (TradingConfig): Единый конфигурационный объект сессии.
        """
        self.config = config
        self.risk_cfg = config.risk_config

        # Определение режима работы (фоллбек на FIXED)
        self.risk_type = self.risk_cfg.get("type", "FIXED")

        # Кэширование параметров (Fixed Mode)
        self.sl_percent = self.risk_cfg.get("stop_loss_pct", 2.0)
        self.tp_ratio = self.risk_cfg.get("take_profit_ratio", 2.0)

        # Кэширование параметров (ATR Mode)
        self.atr_period = self.risk_cfg.get("atr_period", 14)
        self.atr_mult_sl = self.risk_cfg.get("atr_mult_sl", 2.0)
        self.atr_mult_tp = self.risk_cfg.get("atr_mult_tp", 4.0)

        # Money Management
        self.risk_per_trade_pct = self.risk_cfg.get("risk_per_trade_pct", 1.0)

    def calculate(self,
                  entry_price: float,
                  direction: TradeDirection,
                  capital: float,
                  current_candle: Optional[pd.Series] = None) -> TradeRiskProfile:
        """
        Рассчитывает профиль риска для планируемой сделки.

        Выполняет ключевую задачу риск-менеджмента: определение объема позиции (Size)
        таким образом, чтобы при срабатывании стоп-лосса потеря составила ровно
        заданный процент от капитала (`risk_per_trade_pct`).

        Formula:
            Risk_Amount ($) = Capital * (Risk_Pct / 100)
            Risk_Per_Share ($) = |Entry - StopLoss|
            Quantity = Risk_Amount / Risk_Per_Share

        Args:
            entry_price (float): Планируемая цена входа.
            direction (TradeDirection): Направление сделки (BUY/SELL).
            capital (float): Текущий доступный капитал (Equity/Free Margin).
            current_candle (Optional[pd.Series]): Данные текущей свечи (требуется для ATR).

        Returns:
            TradeRiskProfile: Датакласс с рассчитанными ценами (SL, TP) и объемом.
        """
        # 1. Расчет цен Stop Loss и Take Profit
        sl_price, tp_price = self._calculate_stops(entry_price, direction, current_candle)

        # 2. Расчет риска на 1 единицу актива (Risk Per Share)
        # Это дистанция цены, которую мы готовы "потерять" на одной монете/акции.
        risk_per_share = abs(entry_price - sl_price)

        # Защита от деления на ноль (если стоп совпадает с входом или крайне мал)
        if risk_per_share <= 1e-9:
            return TradeRiskProfile(
                stop_loss_price=sl_price,
                take_profit_price=tp_price,
                quantity=0.0,
                risk_amount=0.0
            )

        # 3. Расчет общего денежного риска (Total Risk Amount)
        # Пример: Capital $10,000 * 1% Risk = $100
        risk_amount = capital * (self.risk_per_trade_pct / 100.0)

        # 4. Расчет объема позиции (Position Sizing)
        # Пример: Риск $100 / Риск на акцию $2 = 50 акций
        raw_quantity = risk_amount / risk_per_share

        return TradeRiskProfile(
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            quantity=raw_quantity,
            risk_amount=risk_amount
        )

    def _calculate_stops(self,
                         entry: float,
                         direction: TradeDirection,
                         current_candle: Optional[pd.Series]) -> Tuple[float, float]:
        """
        Маршрутизатор расчета уровней SL/TP.

        Определяет, какой алгоритм использовать.
        Реализует механизм Fallback: если ATR недоступен, откатывается на Fixed.

        Args:
            entry (float): Цена входа.
            direction (TradeDirection): Направление.
            current_candle (Optional[pd.Series]): Данные свечи.

        Returns:
            Tuple[float, float]: (Stop_Loss_Price, Take_Profit_Price).
        """
        if self.risk_type == "ATR":
            atr_stops = self._calculate_atr_stops(entry, direction, current_candle)
            if atr_stops:
                return atr_stops
            # Если ATR вернул None (нет данных), идем дальше к Fixed (Fallback)

        return self._calculate_fixed_stops(entry, direction)

    def _calculate_atr_stops(self,
                             entry: float,
                             direction: TradeDirection,
                             current_candle: Optional[pd.Series]) -> Optional[Tuple[float, float]]:
        """
        Рассчитывает уровни на основе волатильности (ATR).

        Args:
            entry (float): Цена входа.
            direction (TradeDirection): Направление.
            current_candle (Optional[pd.Series]): Свеча с индикаторами.

        Returns:
            Optional[Tuple[float, float]]: Кортеж (SL, TP) или None, если ATR не рассчитан.
        """
        # 1. Валидация наличия данных
        if current_candle is None:
            return None

        atr_col = f"ATR_{self.atr_period}"
        atr_value = current_candle.get(atr_col)

        # 2. Проверка значения индикатора
        if atr_value is None or pd.isna(atr_value):
            return None

        # 3. Расчет дистанций
        sl_dist = atr_value * self.atr_mult_sl
        tp_dist = atr_value * self.atr_mult_tp

        if direction == TradeDirection.BUY:
            return entry - sl_dist, entry + tp_dist
        else:  # SELL
            return entry + sl_dist, entry - tp_dist

    def _calculate_fixed_stops(self, entry: float, direction: TradeDirection) -> Tuple[float, float]:
        """
        Рассчитывает уровни на основе фиксированного процента.

        Args:
            entry (float): Цена входа.
            direction (TradeDirection): Направление.

        Returns:
            Tuple[float, float]: Кортеж (SL, TP).
        """
        sl_dist = entry * (self.sl_percent / 100.0)
        tp_dist = sl_dist * self.tp_ratio

        if direction == TradeDirection.BUY:
            return entry - sl_dist, entry + tp_dist
        else:
            return entry + sl_dist, entry - tp_dist