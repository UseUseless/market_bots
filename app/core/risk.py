"""
Модуль управления рисками (Risk Management).

Этот модуль объединяет в себе логику определения уровней защиты (Stop Loss, Take Profit)
и расчет объема позиции (Position Sizing) на основе допустимого денежного риска.

Классы:
    - **RiskManager**: Единый калькулятор рисков, использующий конфигурацию `TradingConfig`.
"""

from typing import Optional, Tuple
import pandas as pd

from app.shared.schemas import TradingConfig
from app.shared.primitives import TradeDirection, TradeRiskProfile

RISK_MANAGEMENT_TYPES = ["FIXED", "ATR"]

class RiskManager:
    """
    Универсальный менеджер рисков.

    Отвечает за расчет параметров сделки перед входом.
    Поддерживает два режима работы (определяется в `config.risk_config['type']`):
    1. **FIXED**: Стоп-лосс как фиксированный процент от цены.
    2. **ATR**: Стоп-лосс на основе волатильности (индикатор ATR).

    Attributes:
        params_config (Dict): Метаданные параметров для оптимизации (Optuna).
            Описывает диапазоны значений для стопов, тейков и риска на сделку.
    """

    # Конфигурация для оптимизатора (диапазоны перебора)
    params_config = {
        # Параметры для FIXED режима
        "stop_loss_pct": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 0.5, "high": 5.0, "step": 0.1,
            "description": "Stop Loss в % от цены входа (для Fixed mode)."
        },
        "take_profit_ratio": {
            "type": "float", "default": 2.0, "optimizable": True,
            "low": 1.0, "high": 5.0, "step": 0.5,
            "description": "Отношение TP к SL (Risk/Reward Ratio)."
        },
        # Параметры для ATR режима
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
        # Общие параметры
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

        # Определение режима работы
        self.risk_type = self.risk_cfg.get("type", "FIXED")

        # Кэширование параметров (Fixed)
        self.sl_percent = self.risk_cfg.get("stop_loss_pct", 2.0)
        self.tp_ratio = self.risk_cfg.get("take_profit_ratio", 2.0)

        # Кэширование параметров (ATR)
        self.atr_period = self.risk_cfg.get("atr_period", 14)
        self.atr_mult_sl = self.risk_cfg.get("atr_mult_sl", 2.0)
        self.atr_mult_tp = self.risk_cfg.get("atr_mult_tp", 4.0)

        # Money Management
        self.risk_per_trade_pct = self.risk_cfg.get("risk_per_trade_pct", 1.0)

    def calculate(self,
                  entry_price: float,
                  direction: TradeDirection,
                  capital: float,
                  last_candle: Optional[pd.Series] = None) -> TradeRiskProfile:
        """
        Рассчитывает полный профиль риска для планируемой сделки.

        Выполняет расчет уровней SL/TP и определяет объем позиции (Quantity)
        таким образом, чтобы при срабатывании стопа убыток не превысил
        заданный процент от капитала (`risk_per_trade_pct`).

        Args:
            entry_price (float): Планируемая цена входа.
            direction (TradeDirection): Направление сделки (BUY/SELL).
            capital (float): Текущий доступный капитал (Equity).
            last_candle (Optional[pd.Series]): Данные последней свечи.
                Обязательно для режима ATR.

        Returns:
            TradeRiskProfile: Объект с рассчитанными ценами и объемом.
        """
        # 1. Расчет цен Stop Loss и Take Profit
        sl_price, tp_price = self._calculate_stops(entry_price, direction, last_candle)

        # 2. Расчет риска на 1 единицу актива (Risk Per Share)
        risk_per_share = abs(entry_price - sl_price)

        # Защита от деления на ноль (если стоп совпадает с входом)
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
                         candle: pd.Series) -> Tuple[float, float]:
        """
        Внутренняя логика определения ценовых уровней.

        Выбирает алгоритм (ATR или Fixed) в зависимости от настроек.
        """
        # --- Стратегия ATR ---
        if self.risk_type == "ATR":
            if candle is None:
                # В Live-режиме это может случиться, если нет истории.
                # Фоллбек на Fixed, чтобы не крашить бота.
                return self._calculate_fixed_stops(entry, direction)

            atr_col = f"ATR_{self.atr_period}"
            atr_value = candle.get(atr_col)

            # Проверка валидности ATR (он может быть NaN в начале истории)
            if not atr_value or pd.isna(atr_value):
                return self._calculate_fixed_stops(entry, direction)

            sl_dist = atr_value * self.atr_mult_sl
            tp_dist = atr_value * self.atr_mult_tp

            if direction == TradeDirection.BUY:
                sl = entry - sl_dist
                tp = entry + tp_dist
            else:  # SELL
                sl = entry + sl_dist
                tp = entry - tp_dist

            return sl, tp

        # --- Стратегия FIXED (по умолчанию) ---
        else:
            return self._calculate_fixed_stops(entry, direction)

    def _calculate_fixed_stops(self, entry: float, direction: TradeDirection) -> Tuple[float, float]:
        """
        Расчет уровней на основе фиксированного процента от цены входа.
        """
        sl_dist = entry * (self.sl_percent / 100.0)
        tp_dist = sl_dist * self.tp_ratio  # TP считается через Ratio к SL

        if direction == TradeDirection.BUY:
            return entry - sl_dist, entry + tp_dist
        else:
            return entry + sl_dist, entry - tp_dist