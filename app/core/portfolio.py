"""
Модуль управления портфелем (Unified Portfolio).

Этот модуль содержит класс `Portfolio`, который выступает центральным узлом
исполнения торговых операций. Он заменяет собой разрозненные сервисы
(OrderManager, RiskMonitor, Accounting), объединяя данные и поведение.

Основные обязанности:
    1. Хранение состояния (баланс, список активных и закрытых сделок).
    2. Мониторинг рынка (проверка Stop Loss / Take Profit).
    3. Обработка сигналов стратегии (расчет рисков, сайзинг, создание ордеров).
    4. Учет исполненных сделок (создание/закрытие объектов Trade, обновление баланса).
"""

import uuid
from typing import List, Dict, Any, Optional
from queue import Queue

from app.shared.schemas import TradingConfig
from app.shared.primitives import Trade, TradeDirection, TriggerReason
from app.shared.events import SignalEvent, OrderEvent, FillEvent, MarketEvent
from app.core.risk import RiskManager


class Portfolio:
    """
    Единый менеджер портфеля.

    Управляет жизненным циклом сделки от сигнала до фиксации прибыли.
    Использует `RiskManager` для расчетов, но решение об отправке ордеров принимает сам.

    Attributes:
        config (TradingConfig): Конфигурация сессии.
        queue (Queue): Шина событий для отправки ордеров.
        balance (float): Текущий доступный капитал (Free Cash / Equity).
        active_trades (List[Trade]): Список текущих открытых позиций.
        closed_trades (List[Trade]): История закрытых сделок.
        pending_instruments (set): Защита от дублирования ордеров (Idempotency key).
    """

    def __init__(self, config: TradingConfig, events_queue: Queue, instrument_info: Dict[str, Any]):
        """
        Инициализирует портфель.

        Args:
            config (TradingConfig): Единый конфиг.
            events_queue (Queue): Очередь событий.
            instrument_info (Dict): Метаданные инструмента (лотность, шаги).
        """
        self.config = config
        self.queue = events_queue

        # --- Параметры инструмента (для валидации объемов) ---
        self.lot_size = float(instrument_info.get("lot_size", 1.0))
        self.qty_step = float(instrument_info.get("qty_step", 1.0))
        self.min_qty = float(instrument_info.get("min_order_qty", 0.0))

        # --- Состояние Портфеля (State) ---
        self.balance = config.initial_capital
        self.active_trades: List[Trade] = []
        self.closed_trades: List[Trade] = []

        # Множество тикеров, по которым отправлен ордер, но еще нет FillEvent.
        # Блокирует обработку новых сигналов/рисков, пока ордер в пути.
        self.pending_instruments = set()

        # --- Зависимости ---
        self.risk_manager = RiskManager(config)

    # ==================================================================
    # 1. MONITORING (Реакция на рынок)
    # ==================================================================
    def on_market_data(self, event: MarketEvent):
        """
        Обрабатывает рыночные данные (свечу).
        Проверяет, не были ли задеты уровни SL/TP ценами High/Low.

        Args:
            event (MarketEvent): Событие новой свечи.
        """
        candle = event.data
        high = candle['high']
        low = candle['low']
        ts = event.timestamp

        # Итерируемся по копии списка, чтобы безопасно модифицировать оригинал (если потребуется)
        for trade in self.active_trades[:]:
            # Если по инструменту уже идет работа (ордер в пути), пропускаем проверку
            if trade.instrument in self.pending_instruments:
                continue

            # Логика проверки уровней (Pessimistic check: сначала SL, потом TP)
            if trade.direction == TradeDirection.BUY:
                if low <= trade.stop_loss:
                    self._send_exit_order(trade, ts, TriggerReason.STOP_LOSS, trade.stop_loss)
                elif high >= trade.take_profit:
                    self._send_exit_order(trade, ts, TriggerReason.TAKE_PROFIT, trade.take_profit)

            elif trade.direction == TradeDirection.SELL:
                if high >= trade.stop_loss:
                    self._send_exit_order(trade, ts, TriggerReason.STOP_LOSS, trade.stop_loss)
                elif low <= trade.take_profit:
                    self._send_exit_order(trade, ts, TriggerReason.TAKE_PROFIT, trade.take_profit)

    # ==================================================================
    # 2. ORDERING (Реакция на сигнал)
    # ==================================================================
    def on_signal(self, event: SignalEvent, last_candle: Any):
        """
        Обрабатывает сигнал от стратегии.
        Рассчитывает риски и создает ордер на вход или выход.

        Args:
            event (SignalEvent): Входящий сигнал.
            last_candle (pd.Series): Последняя свеча (нужна для расчета ATR и цен).
        """
        # Проверяем, есть ли уже позиция по этому инструменту
        active_trade = next((t for t in self.active_trades if t.symbol == event.instrument), None)

        # Блокировка: если уже висит ордер, игнорируем новые сигналы
        if event.instrument in self.pending_instruments:
            return

        if active_trade:
            # Если позиция есть -> проверяем условие выхода.
            # Сигнал должен быть противоположным (BUY -> SELL или SELL -> BUY).
            is_exit_signal = (
                (active_trade.direction == TradeDirection.BUY and event.direction == TradeDirection.SELL) or
                (active_trade.direction == TradeDirection.SELL and event.direction == TradeDirection.BUY)
            )

            if is_exit_signal:
                # Генерируем выход по рынку (price=None, симулятор исполнит по Open следующей свечи)
                self._send_exit_order(active_trade, event.timestamp, TriggerReason.SIGNAL, price=None)
        else:
            # Если позиции нет -> пытаемся открыть новую.
            self._process_entry_signal(event, last_candle)

    def _process_entry_signal(self, event: SignalEvent, last_candle: Any):
        """Внутренняя логика расчета входа."""
        # 1. Расчет профиля риска (SL/TP, допустимый риск в $)
        risk_profile = self.risk_manager.calculate(
            entry_price=event.price,
            direction=event.direction,
            capital=self.balance,
            last_candle=last_candle
        )

        # 2. Округление объема под требования биржи
        final_qty = self._adjust_quantity(risk_profile.quantity)

        # 3. Если объем валиден — создаем ордер
        if final_qty > 0:
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                direction=event.direction,
                quantity=final_qty,
                trigger_reason=TriggerReason.SIGNAL,
                stop_loss=risk_profile.stop_loss_price,
                take_profit=risk_profile.take_profit_price,
                price=None  # Market Order
            )
            self.queue.put(order)
            self.pending_instruments.add(event.instrument)

    def _send_exit_order(self, trade: Trade, ts, reason: TriggerReason, price: Optional[float] = None):
        """
        Вспомогательный метод для создания закрывающего ордера.
        Закрывает позицию полностью.
        """
        # Направление выхода противоположно направлению позиции
        exit_dir = TradeDirection.SELL if trade.direction == TradeDirection.BUY else TradeDirection.BUY

        order = OrderEvent(
            timestamp=ts,
            instrument=trade.symbol,
            direction=exit_dir,
            quantity=trade.quantity,
            trigger_reason=reason,
            price=price  # Если None -> Market, иначе Limit/Stop
        )
        self.queue.put(order)
        self.pending_instruments.add(trade.symbol)

    # ==================================================================
    # 3. ACCOUNTING (Учет сделок)
    # ==================================================================
    def on_fill(self, event: FillEvent):
        """
        Обрабатывает событие исполнения сделки.
        Обновляет баланс и список сделок.

        Args:
            event (FillEvent): Фактическое исполнение.
        """
        # Снимаем блокировку инструмента
        if event.instrument in self.pending_instruments:
            self.pending_instruments.remove(event.instrument)

        # Пытаемся найти существующую сделку по тикеру
        trade = next((t for t in self.active_trades if t.symbol == event.instrument), None)

        if not trade:
            # Сделки нет -> Это ОТКРЫТИЕ (Entry)
            self._handle_entry_fill(event)
        else:
            # Сделка есть -> Это ЗАКРЫТИЕ (Exit)
            self._handle_exit_fill(event, trade)

    def _handle_entry_fill(self, event: FillEvent):
        """Регистрирует новую позицию."""
        # Списываем стоимость входа (Margin + Commission) из свободного баланса.
        # Упрощение: считаем полное покрытие (1x плечо) для спота и линейных фьючерсов.
        cost = (event.price * event.quantity) + event.commission
        self.balance -= cost

        # Создаем объект сделки
        # Примечание: stop_loss и take_profit должны быть проброшены через FillEvent из OrderEvent
        new_trade = Trade(
            id=str(uuid.uuid4()),
            symbol=event.instrument,
            direction=event.direction,
            entry_time=event.timestamp,
            entry_price=event.price,
            quantity=event.quantity,
            entry_commission=event.commission,
            stop_loss=getattr(event, 'stop_loss', 0.0),
            take_profit=getattr(event, 'take_profit', 0.0)
        )
        self.active_trades.append(new_trade)

    def _handle_exit_fill(self, event: FillEvent, trade: Trade):
        """Закрывает позицию и фиксирует результат."""
        # 1. Вызываем метод закрытия в самом объекте Trade (Инкапсуляция логики PnL)
        trade.close(
            exit_time=event.timestamp,
            exit_price=event.price,
            reason=event.trigger_reason,
            commission=event.commission
        )

        # 2. Возвращаем деньги на баланс.
        # Формула: Вернуть (То, что потратили на вход) + (Чистый PnL) + (Комиссия входа, которую вычли из PnL).
        # Проще: Balance += (EntryPrice * Qty) + PnL + EntryCommission
        # PnL уже содержит вычет обеих комиссий.
        # Пример: Купили на 1000 (ком 1), продали за 1100 (ком 1).
        # Вход: Bal -= 1001.
        # PnL = (1100 - 1000) - 1 - 1 = 98.
        # Выход: Bal += 1000 (тело) + 98 (профит) + 1 (ком входа учтена в PnL) = 1099.
        # Итого Bal: -1001 + 1099 = +98. Верно.

        body_return = trade.entry_price * trade.quantity
        revenue = body_return + trade.pnl + trade.entry_commission

        self.balance += revenue

        # 3. Архивируем сделку
        self.closed_trades.append(trade)
        self.active_trades.remove(trade)

    # ==================================================================
    # 4. UTILITIES (Вспомогательные методы)
    # ==================================================================
    def _adjust_quantity(self, qty: float) -> float:
        """
        Корректирует рассчитанный объем под правила биржи.
        Округляет вниз до шага и проверяет минимальный размер.
        """
        # Округление до шага (например, 0.001)
        if self.qty_step > 0:
            qty = (qty // self.qty_step) * self.qty_step

        # Округление до лота (например, 10 шт)
        if self.lot_size > 1:
            qty = (qty // self.lot_size) * self.lot_size

        # Проверка минимального размера
        return qty if qty >= self.min_qty else 0.0