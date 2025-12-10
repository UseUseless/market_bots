"""
Модуль управления портфелем (Unified Portfolio).

Содержит класс Portfolio, который является монолитным центром исполнения сделок
в режиме симуляции (Бэктест/Оптимизация).

Функции:
1. Хранение состояния (Баланс, Позиции).
2. Валидация правил биржи (Лотность, Шаг объема).
3. Создание ордеров (через RiskManager).
4. Мониторинг выходов (Stop Loss / Take Profit).
5. Учет сделок (Accounting).
"""

import uuid
from typing import List, Dict, Any
from queue import Queue
import math

from app.shared.schemas import TradingConfig
from app.shared.primitives import Trade, TradeDirection, TriggerReason
from app.shared.events import SignalEvent, OrderEvent, FillEvent, MarketEvent
from app.core.risk import RiskManager


class Portfolio:
    def __init__(self, config: TradingConfig, events_queue: Queue, instrument_info: Dict[str, Any]):
        self.config = config
        self.queue = events_queue

        # --- Настройки Инструмента (для округления) ---
        self.lot_size = float(instrument_info.get("lot_size", 1.0))
        self.qty_step = float(instrument_info.get("qty_step", 1.0))
        self.min_qty = float(instrument_info.get("min_order_qty", 0.0))

        # --- Состояние (State) ---
        self.balance = config.initial_capital
        self.active_trades: List[Trade] = []
        self.closed_trades: List[Trade] = []

        # Pending orders (защита от дублей): храним тикеры, по которым ждем исполнения
        self.pending_instruments = set()

        # --- Зависимости ---
        self.risk_manager = RiskManager(config)

    # ==================================================================
    # 1. MONITORING (Бывший RiskMonitor)
    # ==================================================================
    def on_market_data(self, event: MarketEvent):
        """Проверяет, не сработали ли стопы по текущим ценам High/Low."""
        candle = event.data
        high = candle['high']
        low = candle['low']
        ts = event.timestamp

        # Копируем список, чтобы безопасно удалять/модифицировать в цикле
        for trade in self.active_trades[:]:
            # Если по этому инструменту уже висит ордер (например, стратегия послала сигнал),
            # то риск-монитор ждет, чтобы не было конфликта заявок.
            if trade.instrument in self.pending_instruments:
                continue

            # Проверка LONG
            if trade.direction == TradeDirection.BUY:
                if low <= trade.stop_loss:
                    self._send_exit_order(trade, ts, TriggerReason.STOP_LOSS, trade.stop_loss)
                elif high >= trade.take_profit:
                    self._send_exit_order(trade, ts, TriggerReason.TAKE_PROFIT, trade.take_profit)

            # Проверка SHORT
            elif trade.direction == TradeDirection.SELL:
                if high >= trade.stop_loss:
                    self._send_exit_order(trade, ts, TriggerReason.STOP_LOSS, trade.stop_loss)
                elif low <= trade.take_profit:
                    self._send_exit_order(trade, ts, TriggerReason.TAKE_PROFIT, trade.take_profit)

    # ==================================================================
    # 2. ORDERING (Бывший OrderManager)
    # ==================================================================
    def on_signal(self, event: SignalEvent, last_candle: Any):
        """Обрабатывает сигнал стратегии."""
        # Если уже есть открытая позиция
        active_trade = next((t for t in self.active_trades if t.instrument == event.instrument), None)

        if active_trade:
            # Логика выхода по сигналу (разворот пока не поддерживаем, только закрытие)
            # Сигнал должен быть противоположным (BUY -> SELL)
            is_exit = (active_trade.direction == TradeDirection.BUY and event.direction == TradeDirection.SELL) or \
                      (active_trade.direction == TradeDirection.SELL and event.direction == TradeDirection.BUY)

            if is_exit and event.instrument not in self.pending_instruments:
                # Вход по сигналу происходит по цене Open следующей свечи (эмулируется в Engine).
                # Но здесь мы просто отправляем ордер, цену подставит симулятор.
                self._send_exit_order(active_trade, event.timestamp, TriggerReason.SIGNAL, price=None)
            return

        # Если позиции нет -> ВХОД
        if event.instrument in self.pending_instruments:
            return

        self._process_entry_signal(event, last_candle)

    def _process_entry_signal(self, event: SignalEvent, last_candle: Any):
        # Рассчитываем риск
        # Используем цену сигнала (Close) как ориентир для расчета стопов
        risk_profile = self.risk_manager.calculate(
            entry_price=event.price,
            direction=event.direction,
            capital=self.balance,
            last_candle=last_candle
        )

        # Округляем объем под биржу
        final_qty = self._adjust_quantity(risk_profile.quantity)

        if final_qty > 0:
            # Создаем ордер
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                direction=event.direction,
                quantity=final_qty,
                trigger_reason=TriggerReason.SIGNAL,
                stop_loss=risk_profile.stop_loss_price,
                take_profit=risk_profile.take_profit_price,
                price=None # Market order (Open next candle)
            )
            self.queue.put(order)
            self.pending_instruments.add(event.instrument)

    def _send_exit_order(self, trade: Trade, ts, reason: TriggerReason, price: float = None):
        """Helper для отправки закрывающего ордера."""
        exit_dir = TradeDirection.SELL if trade.direction == TradeDirection.BUY else TradeDirection.BUY

        order = OrderEvent(
            timestamp=ts,
            instrument=trade.instrument,
            direction=exit_dir,
            quantity=trade.quantity,
            trigger_reason=reason,
            price=price # Если None, симулятор исполнит по Market
        )
        self.queue.put(order)
        self.pending_instruments.add(trade.instrument)

    # ==================================================================
    # 3. ACCOUNTING (Бывший FillProcessor)
    # ==================================================================
    def on_fill(self, event: FillEvent):
        """Обрабатывает исполнение сделки."""
        # Снимаем блокировку
        if event.instrument in self.pending_instruments:
            self.pending_instruments.remove(event.instrument)

        # Проверяем, это вход или выход?
        # Ищем активную сделку
        trade = next((t for t in self.active_trades if t.instrument == event.instrument), None)

        if not trade:
            # Это ВХОД (новая сделка)
            self._handle_entry_fill(event)
        else:
            # Это ВЫХОД (закрытие)
            self._handle_exit_fill(event, trade)

    def _handle_entry_fill(self, event: FillEvent):
        # Списываем стоимость входа (упрощенная модель для спота/фьючерса без плеча)
        # Баланс уменьшается на маржу + комиссию
        cost = (event.price * event.quantity) + event.commission
        self.balance -= cost

        # Создаем объект Trade
        # SL/TP берем из ордера, который породил этот Fill (в BacktestEngine надо будет прокинуть их)
        # В текущей упрощенной версии Engine мы должны передать SL/TP через Order -> Fill.
        # Допустим, FillEvent содержит эти поля (мы их добавили в предыдущем шаге в OrderEvent,
        # надо убедиться, что Simulator их прокидывает).

        # Примечание: Для корректной работы Simulator должен копировать SL/TP из Order в Fill.
        # Если нет, возьмем их из логики риска заново (но это костыль).
        # Пока предположим, что они придут в событии (или будут 0, если не прокинуты).

        new_trade = Trade(
            id=str(uuid.uuid4()),
            instrument=event.instrument,
            direction=event.direction,
            entry_time=event.timestamp,
            entry_price=event.price,
            quantity=event.quantity,
            entry_commission=event.commission,
            stop_loss=getattr(event, 'stop_loss', 0.0), # Simulator должен добавить это
            take_profit=getattr(event, 'take_profit', 0.0)
        )
        self.active_trades.append(new_trade)

    def _handle_exit_fill(self, event: FillEvent, trade: Trade):
        # Закрываем сделку
        trade.close(
            exit_time=event.timestamp,
            exit_price=event.price,
            reason=event.trigger_reason,
            commission=event.commission
        )

        # Возвращаем деньги (Proceeds)
        # Proceeds = (EntryPrice * Qty) + PnL + EntryCommission (вернуть базу)
        # Проще: Balance += Cost_Basis + PnL
        # Cost_Basis = EntryPrice * Qty

        # Логика PnL уже учла комиссии внутри trade.pnl = Gross - CommEntry - CommExit
        # Но нам надо вернуть "Тело" депозита.

        entry_body = trade.entry_price * trade.quantity

        # Формула изменения баланса:
        # Old_Balance (уже без тела и комсы входа) + Тело + PnL_Gross - Comm_Exit
        # Или проще: Old_Balance + Тело + Trade.PnL + Comm_Entry (т.к. она вычтена в PnL)

        # Самый надежный способ для Cash-based (Спот):
        # Баланс = Баланс + Выручка (Proceeds)
        if trade.direction == TradeDirection.BUY:
            proceeds = (event.price * event.quantity) - event.commission
        else:
            # Для шорта: Мы "продали" по Entry (получили кэш), теперь "покупаем" по Exit (тратим кэш).
            # PnL = (Entry - Exit) * Qty
            # Вернуть маржу + PnL.
            proceeds = (trade.entry_price * trade.quantity) + trade.pnl + trade.entry_commission + trade.exit_commission
            # Тут с шортом на споте сложно, но для фьючерсов обычно PnL просто плюсуется к балансу.
            # Давайте использовать простую модель: Balance += entry_body + trade.pnl
            proceeds = entry_body + trade.pnl + trade.entry_commission + trade.exit_commission

        # Упрощение: Вернуть деньги = (EntryVolume + PnL с учетом комиссий)
        # Но PnL уже чистый. Значит:
        # Balance += (EntryPrice * Qty) + Trade.PnL + Trade.EntryCommission (так как мы ее вычли из PnL, а из баланса она ушла при входе)
        # Нет, стоп.
        # При входе: Balance -= (Entry * Qty + EntryComm)
        # При выходе: Balance += (Entry * Qty) + GrossPnL - ExitComm
        # Trade.PnL = GrossPnL - EntryComm - ExitComm
        # Значит: (Entry * Qty) + GrossPnL - ExitComm = (Entry * Qty) + (Trade.PnL + EntryComm + ExitComm) - ExitComm
        # = (Entry * Qty) + Trade.PnL + EntryComm.

        revenue = (trade.entry_price * trade.quantity) + trade.pnl + trade.entry_commission
        self.balance += revenue

        # Переносим в архив
        self.closed_trades.append(trade)
        self.active_trades.remove(trade)

    # ==================================================================
    # 4. UTILS (Правила биржи)
    # ==================================================================
    def _adjust_quantity(self, qty: float) -> float:
        """Округляет объем под шаг биржи."""
        if self.qty_step > 0:
            qty = (qty // self.qty_step) * self.qty_step

        if self.lot_size > 1:
            qty = (qty // self.lot_size) * self.lot_size

        return qty if qty >= self.min_qty else 0.0