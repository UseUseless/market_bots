"""
Модуль управления портфелем.

Этот модуль содержит класс `Portfolio`.
Отвечает за исполнение торговых операций в симуляции. Он объединяет данные (баланс, позиции)
и поведение (расчет рисков, генерация ордеров, учет сделок).
"""

import uuid
from typing import List, Dict, Any, Optional
from queue import Queue

from app.shared.schemas import TradingConfig
from app.shared.types import Trade, TradeDirection, TriggerReason
from app.shared.events import SignalEvent, OrderEvent, FillEvent, MarketEvent
from app.core.risk import RiskManager


class Portfolio:
    """
    Менеджер портфеля.

    Управляет жизненным циклом сделки от получения сигнала до фиксации прибыли.
    Отвечает за мониторинг позиций, валидацию сигналов через Риск-Менеджер
    и финансовый учет.

    Attributes:
        config (TradingConfig): Конфигу торговой сессии.
        queue (Queue): Очередь событий для отправки ордеров.
        balance (float): Текущий доступный капитал.
        active_trades (List[Trade]): Список текущих открытых позиций.
        closed_trades (List[Trade]): Архив закрытых сделок.
        pending_instruments (set): Множество тикеров с активными, но не исполненными ордерами.
        risk_manager (RiskManager): Компонент расчета рисков.
        lot_size (float): Размер лота инструмента.
        qty_step (float): Шаг изменения количества.
        min_qty (float): Минимальный допустимый объем ордера.
    """

    def __init__(self, config: TradingConfig, events_queue: Queue, instrument_info: Dict[str, Any]):
        """
        Инициализирует портфель.

        Args:
            config: Конфиг.
            events_queue: Очередь для отправки событий (ордеров).
            instrument_info: Метаданные инструмента (lot_size, qty_step, min_qty).
        """
        self.config = config
        self.queue = events_queue

        # Параметры спецификации инструмента для нормализации объемов
        self.lot_size = float(instrument_info.get("lot_size", 1.0))
        self.qty_step = float(instrument_info.get("qty_step", 1.0))
        self.min_qty = float(instrument_info.get("min_order_qty", 0.0))

        # Инициализация состояния
        self.balance = config.initial_capital
        self.active_trades: List[Trade] = []
        self.closed_trades: List[Trade] = []

        # Множество тикеров, по которым отправлен ордер, но еще нет подтверждения (Fill).
        # Используется как блокировка (Lock) для предотвращения дублирования позиций.
        self.pending_instruments = set()

        self.risk_manager = RiskManager(config)

    def on_market_data(self, event: MarketEvent):
        """
        Обрабатывает новую свечу.

        Проверяет все активные позиции на предмет срабатывания условий выхода
        (Stop Loss или Take Profit) внутри диапазона High-Low текущей свечи.

        Args:
            event (MarketEvent): Событие, содержащее данные свечи.
        """
        current_candle = event.candle
        high = current_candle['high']
        low = current_candle['low']
        ts = event.timestamp

        # Итерируемся по копии списка, чтобы безопасно изменять его (если потребуется)
        for trade in self.active_trades[:]:
            # Пропускаем инструменты, по которым уже висит активный ордер
            if trade.symbol in self.pending_instruments:
                continue

            # Пессимистичная проверка уровней: сначала SL, потом TP
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

    def on_signal(self, event: SignalEvent, current_candle: Any):
        """
        Обрабатывает торговый сигнал от стратегии.

        Принимает решение об открытии, закрытии или игнорировании сигнала
        в зависимости от текущего состояния портфеля.

        Args:
            event (SignalEvent): Входящий сигнал.
            current_candle (pd.Series): Данные текущей свечи (нужны для расчета ATR рисков).
        """
        # Блокировка: если ордер в пути, игнорируем новые сигналы
        if event.instrument in self.pending_instruments:
            return

        # Поиск существующей позиции по инструменту
        active_trade = next((t for t in self.active_trades if t.symbol == event.instrument), None)

        if active_trade:
            # Логика разворота или выхода:
            # Если сигнал противоположен позиции -> Закрываем текущую.
            is_exit_signal = (
                (active_trade.direction == TradeDirection.BUY and event.direction == TradeDirection.SELL) or
                (active_trade.direction == TradeDirection.SELL and event.direction == TradeDirection.BUY)
            )

            if is_exit_signal:
                # Отправляем Market Order на выход
                self._send_exit_order(active_trade, event.timestamp, TriggerReason.SIGNAL, price=None)
        else:
            # Если позиции нет -> Рассчитываем вход
            self._process_entry_signal(event, current_candle)

    def _process_entry_signal(self, event: SignalEvent, current_candle: Any):
        """
        Рассчитывает параметры входа (Risk Sizing) и генерирует ордер.

        Также выполняет проверку покупательной способности (Margin Check):
        если свободных средств не хватает, объем позиции уменьшается.

        Args:
            event (SignalEvent): Входящий сигнал.
            current_candle (Any): Текущая свеча для расчетов риска.
        """
        # 1. Расчет профиля риска (SL/TP, Qty) через RiskManager
        risk_profile = self.risk_manager.calculate(
            entry_price=event.price,
            direction=event.direction,
            capital=self.balance,
            current_candle=current_candle
        )

        # 2. Нормализация объема под спецификацию инструмента
        final_qty = self._adjust_quantity(risk_profile.quantity)

        # Проверяем, хватит ли денег на открытие позиции + комиссию
        estimated_cost = final_qty * event.price
        estimated_commission = estimated_cost * self.config.commission_rate
        total_required = estimated_cost + estimated_commission

        if total_required > self.balance:
            # Денег не хватает. Пытаемся уменьшить позу под остаток (re-calculate).
            # Формула: Qty = (Balance / (Price * (1 + CommRate)))
            max_qty_by_cash = self.balance / (event.price * (1 + self.config.commission_rate))
            final_qty = self._adjust_quantity(max_qty_by_cash)
            
            # Если даже минимальный лот не лезет — отмена
            if final_qty < self.min_qty:
                return 

        # 3. Генерация ордера, если объем валиден
        if final_qty > 0:
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                direction=event.direction,
                quantity=final_qty,
                trigger_reason=TriggerReason.SIGNAL,
                stop_loss=risk_profile.stop_loss_price,
                take_profit=risk_profile.take_profit_price,
                price=None  # Market Order (исполнение по Open следующей свечи)
            )
            self.queue.put(order)
            self.pending_instruments.add(event.instrument)

    def _send_exit_order(self, trade: Trade, ts, reason: TriggerReason, price: Optional[float] = None):
        """
        Вспомогательный метод для генерации закрывающего ордера.

        Args:
            trade (Trade): Инструмент активной сделки, которую нужно закрыть.
            ts (datetime): Время события.
            reason (TriggerReason): Причина выхода (SL, TP, Signal).
            price (Optional[float]): Цена выхода (для Limit/Stop) или None (для Market).
        """
        # Направление выхода всегда противоположно направлению входа
        exit_dir = TradeDirection.SELL if trade.direction == TradeDirection.BUY else TradeDirection.BUY

        order = OrderEvent(
            timestamp=ts,
            instrument=trade.symbol,
            direction=exit_dir,
            quantity=trade.quantity,
            trigger_reason=reason,
            price=price  # Если цена задана -> Limit/Stop, если None -> Market
        )
        self.queue.put(order)
        self.pending_instruments.add(trade.symbol)

    # --- Execution & Accounting Section ---

    def on_fill(self, event: FillEvent):
        """
        Обрабатывает подтверждение исполнения сделки (Fill).

        Обновляет баланс, переносит сделки между списками активных/закрытых.

        Args:
            event (FillEvent): Данные о фактическом исполнении.
        """
        # Снятие блокировки инструмента
        if event.instrument in self.pending_instruments:
            self.pending_instruments.remove(event.instrument)

        trade = next((t for t in self.active_trades if t.symbol == event.instrument), None)

        if not trade:
            self._handle_entry_fill(event)
        else:
            self._handle_exit_fill(event, trade)

    def _handle_entry_fill(self, event: FillEvent):
        """
        Регистрация новой позиции (Entry).
        Списывает стоимость позиции и комиссию с баланса.

        Args:
            event (FillEvent): Событие исполнения ордера на вход.
        """
        # Списание стоимости позиции (Margin) и комиссии из свободного баланса.
        # Упрощенная модель: 100% покрытие (плечо 1x).
        cost = (event.price * event.quantity) + event.commission
        self.balance -= cost

        # Создание объекта сделки
        new_trade = Trade(
            id=str(uuid.uuid4()),
            symbol=event.instrument,
            direction=event.direction,
            strategy_name=self.config.strategy_name,
            entry_time=event.timestamp,
            entry_price=event.price,
            quantity=event.quantity,
            entry_commission=event.commission,
            # SL/TP пробрасываются из OrderEvent -> FillEvent
            stop_loss=getattr(event, 'stop_loss', 0.0),
            take_profit=getattr(event, 'take_profit', 0.0)
        )
        self.active_trades.append(new_trade)

    def _handle_exit_fill(self, event: FillEvent, trade: Trade):
        """
        Закрытие позиции и фиксация результата (Exit).
        Возвращает средства на баланс и переносит сделку в архив.

        Логика возврата средств (Cash Flow):
        Мы возвращаем себе "Тело позиции" (которое списали при входе)
        плюс "Грязный PnL" (разницу цен) минус "Комиссию за выход".

        Args:
            event (FillEvent): Событие исполнения ордера на выход.
            trade (Trade): Объект сделки, которая закрывается.
        """
        # 1. Считаем Грязный PnL (Gross PnL) - чисто разница курсов без комиссий
        if trade.direction == TradeDirection.BUY:
            gross_pnl = (event.price - trade.entry_price) * trade.quantity
        else:
            gross_pnl = (trade.entry_price - event.price) * trade.quantity

        # 2. Закрываем сделку в статистике (тут считается чистый PnL для отчетов)
        trade.close(
            exit_time=event.timestamp,
            exit_price=event.price,
            reason=event.trigger_reason,
            commission=event.commission
        )

        # 3. Движение денег (Возврат на баланс)
        # Нам возвращается:
        # + Деньги, которые мы вложили (Locked Margin / Body)
        # + То, что мы наторговали (Gross PnL)
        # - То, что забрала биржа за выход (Exit Commission)
        
        initial_margin = trade.entry_price * trade.quantity
        returned_cash = initial_margin + gross_pnl - event.commission
        
        self.balance += returned_cash

        # 4. Архивация
        self.closed_trades.append(trade)
        self.active_trades.remove(trade)

    def _adjust_quantity(self, qty: float) -> float:
        """
        Корректирует объем позиции в соответствии с правилами биржи.
        Округляет вниз до шага лота и проверяет минимальный размер.

        Args:
            qty (float): Расчетный объем.

        Returns:
            float: Скорректированный объем или 0.0, если он меньше минимума.
        """
        if self.qty_step > 0:
            qty = (qty // self.qty_step) * self.qty_step

        if self.lot_size > 1:
            qty = (qty // self.lot_size) * self.lot_size

        return qty if qty >= self.min_qty else 0.0