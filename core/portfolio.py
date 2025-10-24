from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent
from utils.trade_logger import log_trade
from strategies.base_strategy import BaseStrategy

class Portfolio:
    """
    Управляет состоянием счета, позициями и генерирует ордера.
    Выступает в роли риск-менеджера и реалистичного симулятора исполнения.
    """
    def __init__(self, events_queue: Queue, trade_log_file: str, strategy: BaseStrategy, initial_capital=100000.0, commission_rate=0.0005):
        self.events_queue = events_queue
        self.trade_log_file = trade_log_file
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.commission_rate = commission_rate

        self.current_positions = {}
        self.pending_orders = set()
        self.closed_trades = []
        self.last_market_data = {}

    def update_market_price(self, event: MarketEvent):
        """Обновляет рыночные данные и проверяет SL/TP на каждой новой свече."""
        figi = event.figi
        self.last_market_data[figi] = event.data
        
        position = self.current_positions.get(figi)
        if not position or position.get('exit_pending'): # Если позиции нет или она уже закрывается, выходим
            return

        exit_reason = None
        candle_high = event.data['high']
        candle_low = event.data['low']

        if position['direction'] == 'BUY':
            if candle_low <= position['stop_loss']:
                exit_reason = "Stop Loss"
            elif candle_high >= position['take_profit']:
                exit_reason = "Take Profit"
        # Сюда можно добавить логику для шортов
        
        if exit_reason:     # Логика выхода из существующей позиции !ПО СТОП_ЛОСС ИЛИ ТЕЙК_ПРОФИТ И ТОЛЬКО ПО НИМ!
            logging.warning(f"!!! СРАБОТАЛ {exit_reason.upper()} для {figi}. Генерирую ордер на закрытие.")
            order = OrderEvent(figi=figi, quantity=position['quantity'], direction="SELL")
            self.events_queue.put(order)
            self.pending_orders.add(figi)
            # Помечаем позицию как "ожидающую закрытия", чтобы избежать дублирования ордеров.
            self.current_positions[figi]['exit_pending'] = exit_reason

    def on_signal(self, event: SignalEvent):
        """Обрабатывает сигнал от стратегии и решает, отправлять ли ордер."""
        figi = event.figi
        position = self.current_positions.get(figi)

        # Фильтр: Игнорируем сигналы, если уже есть позиция, ожидающий ордер, или позиция закрывается по SL/TP
        if (position and position.get('exit_pending')) or figi in self.pending_orders:
            return

        if not position: # Логика входа в новую позицию
            if event.direction == "BUY":
                order = OrderEvent(figi=figi, quantity=1, direction="BUY")
                self.events_queue.put(order)
                self.pending_orders.add(figi)
                logging.info(f"Портфель генерирует ордер на ПОКУПКУ {figi}")
        else: # Логика выхода из существующей позиции !ПО СИГНАЛУ И ТОЛЬКО ПО СИГНАЛУ! (если есть купленный лот, а сигнал на продажу и наоборот)
            if event.direction == "SELL" and position['direction'] == 'BUY':
                order = OrderEvent(figi=figi, quantity=position['quantity'], direction="SELL")
                self.events_queue.put(order)
                self.pending_orders.add(figi) # Также помечаем ордер на закрытие как ожидающий
                logging.info(f"Портфель генерирует ордер на ПРОДАЖУ (закрытие) {figi}")

    def on_fill(self, event: FillEvent):
        """Обновляет состояние позиций после фактического исполнения ордера."""
        figi = event.figi
        
        # Убираем ордер из списка ожидающих, если он там был
        if figi in self.pending_orders:
            self.pending_orders.remove(figi)
        
        last_candle = self.last_market_data.get(figi)
        if last_candle is None:
            logging.error(f"Нет рыночных данных для исполнения ордера по {figi}")
            return
        
        position = self.current_positions.get(figi)
        
        # Сценарий 1: Открытие новой позиции
        if not position:
            execution_price = last_candle['open']
            sl_percent = self.strategy.stop_loss_percent / 100.0
            tp_percent = self.strategy.take_profit_percent / 100.0
            
            if event.direction == 'BUY':
                stop_loss_price = execution_price * (1 - sl_percent)
                take_profit_price = execution_price * (1 + tp_percent)
            else: # Для шорта (пока не реализовано)
                stop_loss_price = execution_price * (1 + sl_percent)
                take_profit_price = execution_price * (1 - tp_percent)

            self.current_positions[figi] = {
                'quantity': event.quantity, 'entry_price': execution_price,
                'direction': event.direction, 'stop_loss': stop_loss_price,
                'take_profit': take_profit_price, 'exit_pending': None
            }
            logging.info(f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {figi} @ {execution_price:.2f} | SL: {stop_loss_price:.2f}, TP: {take_profit_price:.2f}")
        
        # Сценарий 2: Закрытие существующей позиции
        # Да, проверка лишняя (можно просто else), но она добавляет наглядности
        elif event.direction != position['direction']:
            entry_price = position['entry_price']
            
            # Определяем цену и причину выхода
            if position.get('exit_pending') == "Stop Loss":
                exit_price = position['stop_loss']
                exit_reason = "Stop Loss"
            elif position.get('exit_pending') == "Take Profit":
                exit_price = position['take_profit']
                exit_reason = "Take Profit"
            else: # Закрытие по сигналу от стратегии
                exit_price = last_candle['open']
                exit_reason = "Signal"

            # Рассчитываем комиссию за вход и выход
            commission = (entry_price * event.quantity + exit_price * event.quantity) * self.commission_rate
            
            # Рассчитываем PnL
            if position['direction'] == 'BUY':
                pnl = (exit_price - entry_price) * event.quantity - commission
            else: # Для шорта
                pnl = (entry_price - exit_price) * event.quantity - commission
            
            self.current_capital += pnl
            
            # Логируем сделку в CSV
            log_trade(
                trade_log_file=self.trade_log_file, strategy_name=self.strategy.name,
                figi=figi, direction=position['direction'], entry_price=entry_price,
                exit_price=exit_price, pnl=pnl, exit_reason=exit_reason
            )
            # Сохраняем информацию о сделке для финального отчета
            self.closed_trades.append({'pnl': pnl})

            # Окончательно удаляем позицию из списка активных
            del self.current_positions[figi]
            logging.info(f"Позиция ЗАКРЫТА по причине '{exit_reason}': {figi}. PnL: {pnl:.2f}. Капитал: {self.current_capital:.2f}")