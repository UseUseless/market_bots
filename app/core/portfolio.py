"""
Модуль управления портфелем (Unified Portfolio).

Этот модуль содержит класс `Portfolio`, который выступает центральным узлом
исполнения торговых операций в симуляции. Он объединяет данные (баланс, позиции)
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
    Единый менеджер портфеля.

    Управляет жизненным циклом сделки от получения сигнала до фиксации прибыли.
    Отвечает за:
    1. Мониторинг открытых позиций (проверка SL/TP).
    2. Валидацию сигналов и расчет объема позиции (Risk Management).
    3. Финансовый учет (Accounting) и обновление баланса.

    Attributes:
        config (TradingConfig): Конфигурация торговой сессии.
        queue (Queue): Шина событий для отправки ордеров.
        balance (float): Текущий доступный капитал (Free Cash).
        active_trades (List[Trade]): Список текущих открытых позиций.
        closed_trades (List[Trade]): Архив закрытых сделок.
        risk_manager (RiskManager): Компонент расчета рисков.
    """

    def __init__(self, config: TradingConfig, events_queue: Queue, instrument_info: Dict[str, Any]):
        """
        Инициализирует портфель.

        Args:
            config: Единый объект конфигурации.
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

    # --- Market Monitoring Section ---

    def on_market_data(self, event: MarketEvent):
        """
        Обрабатывает обновление рыночных данных (новую свечу).

        Проверяет все активные позиции на предмет срабатывания условий выхода
        (Stop Loss или Take Profit) внутри диапазона High-Low текущей свечи.

        Args:
            event (MarketEvent): Событие, содержащее данные свечи.
        """
        candle = event.data
        high = candle['high']
        low = candle['low']
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

    # --- Signal Processing Section ---

    def on_signal(self, event: SignalEvent, last_candle: Any):
        """
        Обрабатывает торговый сигнал от стратегии.

        Принимает решение об открытии, закрытии или игнорировании сигнала
        в зависимости от текущего состояния портфеля.

        Args:
            event (SignalEvent): Входящий сигнал.
            last_candle (pd.Series): Данные последней свечи (нужны для расчета ATR рисков).
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
            self._process_entry_signal(event, last_candle)

    def _process_entry_signal(self, event: SignalEvent, last_candle: Any):
        """
        Рассчитывает параметры входа (Risk Sizing) и генерирует ордер.
        """
        # 1. Расчет профиля риска (SL/TP, Qty) через RiskManager
        risk_profile = self.risk_manager.calculate(
            entry_price=event.price,
            direction=event.direction,
            capital=self.balance,
            last_candle=last_candle
        )

        # 2. Нормализация объема под спецификацию инструмента
        final_qty = self._adjust_quantity(risk_profile.quantity)

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
        """Регистрация новой позиции (Entry)."""
        # Списание стоимости позиции (Margin) и комиссии из свободного баланса.
        # Упрощенная модель: 100% покрытие (плечо 1x).
        cost = (event.price * event.quantity) + event.commission
        self.balance -= cost

        # Создание объекта сделки
        new_trade = Trade(
            id=str(uuid.uuid4()),
            symbol=event.instrument,
            direction=event.direction,
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
        """Закрытие позиции и фиксация результата (Exit)."""
        # 1. Расчет PnL внутри объекта Trade
        trade.close(
            exit_time=event.timestamp,
            exit_price=event.price,
            reason=event.trigger_reason,
            commission=event.commission
        )

        # 2. Возврат средств на баланс
        # Возвращаем: Тело позиции (EntryPrice * Qty) + Чистый PnL + Комиссия входа (была вычтена ранее)
        # Формула вывода: Balance += Revenue
        body_return = trade.entry_price * trade.quantity
        # PnL уже очищен от обеих комиссий, поэтому добавляем их обратно для корректного баланса
        # (т.к. комиссия выхода списывается из профита, а комиссия входа была списана при входе)
        # Упрощенно: Balance += (Body + PnL + EntryComm) - фактически мы возвращаем остаток.

        revenue = body_return + trade.pnl + trade.entry_commission
        self.balance += revenue

        # 3. Архивация
        self.closed_trades.append(trade)
        self.active_trades.remove(trade)

    # --- Utilities ---

    def _adjust_quantity(self, qty: float) -> float:
        """
        Корректирует объем позиции в соответствии с правилами биржи.
        Округляет вниз до шага лота.
        """
        if self.qty_step > 0:
            qty = (qty // self.qty_step) * self.qty_step

        if self.lot_size > 1:
            qty = (qty // self.lot_size) * self.lot_size

        return qty if qty >= self.min_qty else 0.0