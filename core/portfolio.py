from queue import Queue
import pandas as pd
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

        # Хранит информацию о текущих открытых позициях
        # Формат: {'FIGI': {'quantity': 1, 'entry_price': 150.0, 'direction': 'BUY', 'stop_loss': 148.5, 'take_profit': 153.0}}
        self.current_positions = {}
        
        # Хранит историю всех закрытых сделок для финальной статистики
        self.closed_trades = []
        
        # Хранит всю последнюю свечу для каждого инструмента
        self.last_market_data = {}

    def update_market_price(self, event: MarketEvent):
        """Обновляет рыночные данные И ПРОВЕРЯЕТ SL/TP на каждой новой свече."""
        figi = event.figi
        self.last_market_data[figi] = event.data
        
        position = self.current_positions.get(figi)
        if not position:
            return

        # --- ЛОГИКА ПРОВЕРКИ SL/TP ---
        exit_reason = None
        candle_high = event.data['high']
        candle_low = event.data['low']

        if position['direction'] == 'BUY':
            if candle_low <= position['stop_loss']:
                exit_reason = "Stop Loss"
            elif candle_high >= position['take_profit']:
                exit_reason = "Take Profit"
        # Сюда можно добавить логику для шортов
        
        if exit_reason:
            logging.warning(f"!!! СРАБОТАЛ {exit_reason.upper()} для {figi}. Генерирую ордер на закрытие.")
            order = OrderEvent(figi=figi, quantity=position['quantity'], direction="SELL")
            self.events_queue.put(order)
            
            # Логируем сделку немедленно, используя точную цену SL/TP как цену выхода
            exit_price = position['stop_loss'] if exit_reason == "Stop Loss" else position['take_profit']
            entry_price = position['entry_price']
            
            # Рассчитываем PnL и обновляем капитал
            pnl = (exit_price - entry_price) * position['quantity'] # Упрощенный PnL без комиссии для лога
            self.current_capital += pnl # Приблизительное обновление капитала
            
            # Записываем сделку в CSV и в историю для отчета
            log_trade(
                trade_log_file=self.trade_log_file, strategy_name=self.strategy.name,
                figi=figi, direction=position['direction'], entry_price=entry_price,
                exit_price=exit_price, pnl=pnl, exit_reason=exit_reason
            )
            self.closed_trades.append({'pnl': pnl}) # Добавляем PnL для финального отчета
            
            # Удаляем позицию, чтобы избежать повторных ордеров на закрытие
            del self.current_positions[figi]

    def on_signal(self, event: SignalEvent):
        """Обрабатывает сигнал от стратегии и решает, отправлять ли ордер."""
        figi = event.figi
        position = self.current_positions.get(figi)

        if event.direction == "BUY" and not position:
            order = OrderEvent(figi=figi, quantity=1, direction="BUY")
            self.events_queue.put(order)
            logging.info(f"Портфель генерирует ордер на ПОКУПКУ {figi}")
        elif event.direction == "SELL" and position and position['direction'] == 'BUY':
            order = OrderEvent(figi=figi, quantity=position['quantity'], direction="SELL")
            self.events_queue.put(order)
            logging.info(f"Портфель генерирует ордер на ПРОДАЖУ (закрытие) {figi}")

    def on_fill(self, event: FillEvent):
        """Обновляет состояние позиций, используя цену ОТКРЫТИЯ свечи."""
        figi = event.figi
        
        # --- ЛОГИКА РЕАЛИСТИЧНОГО ИСПОЛНЕНИЯ ---
        last_candle = self.last_market_data.get(figi)
        if last_candle is None:
            logging.error(f"Нет рыночных данных для исполнения ордера по {figi}")
            return
        execution_price = last_candle['open'] # <-- Используем цену открытия!
        # ---------------------------------------------------

        commission = execution_price * event.quantity * self.commission_rate
        position = self.current_positions.get(figi)

        if not position: # Открытие новой позиции
            sl_percent = self.strategy.stop_loss_percent / 100.0
            tp_percent = self.strategy.take_profit_percent / 100.0
            
            if event.direction == 'BUY':
                stop_loss_price = execution_price * (1 - sl_percent)
                take_profit_price = execution_price * (1 + tp_percent)
            else: # Для шорта
                stop_loss_price = execution_price * (1 + sl_percent)
                take_profit_price = execution_price * (1 - tp_percent)

            self.current_positions[figi] = {
                'quantity': event.quantity, 'entry_price': execution_price,
                'direction': event.direction, 'stop_loss': stop_loss_price,
                'take_profit': take_profit_price
            }
            logging.info(f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {figi} @ {execution_price:.2f} | SL: {stop_loss_price:.2f}, TP: {take_profit_price:.2f}")
        
        elif event.direction != position['direction']: # Закрытие позиции по сигналу
            entry_price = position['entry_price']
            pnl = (execution_price - entry_price) * event.quantity - commission
            self.current_capital += pnl
            
            log_trade(
                trade_log_file=self.trade_log_file, strategy_name=self.strategy.name,
                figi=figi, direction=position['direction'], entry_price=entry_price,
                exit_price=execution_price, pnl=pnl, exit_reason="Signal"
            )
            self.closed_trades.append({'pnl': pnl})

            del self.current_positions[figi]
            logging.info(f"Позиция ЗАКРЫТА по сигналу: {figi}. PnL: {pnl:.2f}. Капитал: {self.current_capital:.2f}")

    def generate_performance_report(self):
        """Генерирует и выводит отчет о результатах торговли."""
        if not self.closed_trades:
            print("\n--- Отчет о производительности ---")
            print("Сделок не было совершено.")
            return

        df = pd.DataFrame(self.closed_trades)
        total_pnl = df['pnl'].sum()
        win_trades = (df['pnl'] > 0).sum()
        total_trades = len(df)
        win_rate = (win_trades / total_trades) * 100 if total_trades > 0 else 0

        print("\n--- Отчет о производительности ---")
        print(f"Начальный капитал: {self.initial_capital:.2f}")
        print(f"Конечный капитал:  {self.current_capital:.2f}")
        print(f"Общий PnL:         {total_pnl:.2f} ({total_pnl/self.initial_capital*100:.2f}%)")
        print(f"Всего сделок:      {total_trades}")
        print(f"Прибыльных сделок: {win_trades}")
        print(f"Win Rate:          {win_rate:.2f}%")
        print("---------------------------------")