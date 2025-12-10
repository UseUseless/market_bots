"""
Модуль управления рисками (Risk Management).

Объединяет расчет уровней защиты (SL/TP) и расчет объема позиции (Sizing).
Работает на основе конфигурации TradingConfig.
"""

from typing import Dict, Optional, Tuple
import pandas as pd

from app.shared.schemas import TradingConfig
from app.shared.primitives import TradeDirection, TradeRiskProfile


class RiskManager:
    """
    Единый калькулятор рисков.

    Отвечает за два вопроса:
    1. По какой цене выходить (Stop Loss / Take Profit)?
    2. Каким объемом входить (Quantity)?
    """

    def __init__(self, config: TradingConfig):
        self.config = config
        self.risk_cfg = config.risk_config

        # Кэшируем параметры для быстрого доступа
        self.risk_type = self.risk_cfg.get("type", "FIXED")

        # Параметры для FIXED
        self.sl_percent = self.risk_cfg.get("stop_loss_pct", 2.0)
        self.tp_ratio = self.risk_cfg.get("take_profit_ratio", 2.0)

        # Параметры для ATR
        self.atr_period = self.risk_cfg.get("atr_period", 14)
        self.atr_mult_sl = self.risk_cfg.get("atr_mult_sl", 2.0)
        self.atr_mult_tp = self.risk_cfg.get("atr_mult_tp", 4.0)

        # Риск на сделку (% от капитала)
        self.risk_per_trade_pct = self.risk_cfg.get("risk_per_trade_pct", 1.0)

    def calculate(self, entry_price: float, direction: TradeDirection,
                  capital: float, last_candle: Optional[pd.Series] = None) -> TradeRiskProfile:
        """
        Главный метод расчета.
        """
        # 1. Расчет цен SL / TP
        sl_price, tp_price = self._calculate_stops(entry_price, direction, last_candle)

        # 2. Расчет риска на единицу актива (разница цен)
        risk_per_share = abs(entry_price - sl_price)

        # Если стоп слишком близко (деление на 0) -> объем 0
        if risk_per_share <= 1e-9:
            return TradeRiskProfile(sl_price, tp_price, 0.0, 0.0)

        # 3. Расчет допустимого денежного риска
        # Например: $10000 * 1% = $100
        risk_amount = capital * (self.risk_per_trade_pct / 100.0)

        # 4. Расчет объема (Quantity)
        # $100 / $2 (риск на акцию) = 50 акций
        raw_quantity = risk_amount / risk_per_share

        return TradeRiskProfile(
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            quantity=raw_quantity,  # Неокругленный объем! Округление будет в Portfolio.
            risk_amount=risk_amount
        )

    def _calculate_stops(self, entry: float, direction: TradeDirection,
                         candle: pd.Series) -> Tuple[float, float]:
        """Внутренняя логика определения уровней."""
        sl, tp = 0.0, 0.0

        # --- Логика ATR ---
        if self.risk_type == "ATR":
            if candle is None:
                raise ValueError("Для ATR-риска нужны данные свечи (last_candle)")

            atr_col = f"ATR_{self.atr_period}"
            atr_value = candle.get(atr_col)

            if not atr_value or pd.isna(atr_value):
                # Fallback на фиксированный процент, если ATR еще не рассчитался
                return self._calculate_fixed_stops(entry, direction)

            sl_dist = atr_value * self.atr_mult_sl
            tp_dist = atr_value * self.atr_mult_tp

            if direction == TradeDirection.BUY:
                sl = entry - sl_dist
                tp = entry + tp_dist
            else:
                sl = entry + sl_dist
                tp = entry - tp_dist

        # --- Логика FIXED ---
        else:
            return self._calculate_fixed_stops(entry, direction)

        return sl, tp

    def _calculate_fixed_stops(self, entry: float, direction: TradeDirection) -> Tuple[float, float]:
        """Расчет процентов от цены входа."""
        sl_dist = entry * (self.sl_percent / 100.0)
        tp_dist = sl_dist * self.tp_ratio

        if direction == TradeDirection.BUY:
            return entry - sl_dist, entry + tp_dist
        else:
            return entry + sl_dist, entry - tp_dist