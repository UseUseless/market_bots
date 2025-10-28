from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent
from utils.trade_logger import log_trade
from strategies.base_strategy import BaseStrategy

class Portfolio:
    """
    Управляет состоянием счета, позициями и генерирует ордера.
    Выступает в роли риск-менеджера и логического центра программы, управляющего событиями.
    """
    def __init__(self, events_queue: Queue, trade_log_file: str, strategy: BaseStrategy, initial_capital: float, commission_rate: float):
        self.events_queue = events_queue        # Ссылка на общую очередь событий для отправки ордеров
        self.trade_log_file = trade_log_file    # Путь к CSV-файлу для записи сделок
        self.strategy = strategy                # Экземпляр текущей стратегии (нужен для доступа к SL/TP)
        self.initial_capital = initial_capital  # Начальный капитал для бэктеста
        self.current_capital = initial_capital  # Текущий капитал, изменяется после каждой сделки
        self.commission_rate = commission_rate  # Размер комиссии в долях Ex:(0.0005 = 0.05%)

        # Словарь для хранения всех активных позиций. Ключ - FIGI, значение - словарь с деталями позиции.
        self.current_positions = {}
        # Set для хранения FIGI, по которым был отправлен ордер, но еще не пришел отчет об исполнении (FillEvent).
        self.pending_orders = set()
        # Информация о всех закрытых сделках для финального отчета.
        self.closed_trades = []
        # Словарь для хранения последней полученной свечи по каждому инструменту.
        # Нужно для определения цены исполнения ордера (покупка, продажа)
        self.last_market_data = {}

    def update_market_price(self, event: MarketEvent):
        """
        Вызывается на КАЖДЫЙ MarketEvent (новую свечу)
        Обновляет рыночные данные
        Проверяет SL/TP на каждой новой свече (если есть позиция).
        """
        # Получаем FIGI и данные из события
        figi = event.figi
        self.last_market_data[figi] = event.data

        # Данные получили
        # Код дальше выполняется только если есть позиция по какому-то инструменту

        # Получаем текущую позицию по этому инструменту
        position = self.current_positions.get(figi)
        # Если позиции нет или по этому инструменту есть ордер, то ничего не делаем больше
        if not position or figi in self.pending_orders:
            return

        exit_reason = None
        # Получаем максимальную и минимальную цену за период свечи
        candle_high = event.data['high']
        candle_low = event.data['low']

        # Проверяем условия SL/TP для лонга
        if position['direction'] == 'BUY':
            # Если минимальная цена свечи коснулась или пробила наш стоп-лосс
            if candle_low <= position['stop_loss']:
                exit_reason = "Stop Loss"
            # Если максимальная цена свечи коснулась или пробила наш тейк-профит
            elif candle_high >= position['take_profit']:
                exit_reason = "Take Profit"
        # ToDo: Добавить случай для 'Sell' - то есть для шорта

        # Логика выхода из существующей позиции !ПО СТОП_ЛОСС ИЛИ ТЕЙК_ПРОФИТ И ТОЛЬКО ПО НИМ!
        # Закрытие по сигналу в on_signal
        if exit_reason:
            logging.warning(f"!!! СРАБОТАЛ {exit_reason.upper()} для {figi}. Генерирую ордер на закрытие.")
            # Создаем приказ (OrderEvent) на продажу для закрытия позиции
            order = OrderEvent(figi=figi, quantity=position['quantity'], direction="SELL")
            # Кладем OrderEvent в общую очередь событий
            self.events_queue.put(order)
            # Добавляем ордер в список ожидающих на исполнение, чтобы его исполнить в первую очередь
            self.pending_orders.add(figi)
            # Помечаем позицию как "ожидающую закрытия", чтобы избежать дублирования ордеров и пишем причину
            self.current_positions[figi]['exit_reason'] = exit_reason

    def on_signal(self, event: SignalEvent):
        """Обрабатывает сигнал от стратегии и решает, отправлять ли ордер."""
        figi = event.figi
        position = self.current_positions.get(figi)

        # Фильтр: Игнорируем сигналы, если ордер по инструменту в обработке.
        # Так как мы могли в очередь добавить через update_market_price OrderEvent
        # Но мы еще не до конца обработали MarketEvent и от него пришел SignalEvent в очередь
        # У нас в очереди в таком порядке два события: OrderEvent, SignalEvent
        # Мы обрабатываем OrderEvent и он превращается в FillEvent и становится вторым
        # У нас в очереди в таком порядке два события: SignalEvent, FillEvent
        # Запускается этот метод и для игнорирования SignalEvent проверяем pending_orders
        # Переменная pending_orders не пуста, так как она всегда заполняется когда создается OrderEvent
        if figi in self.pending_orders:
            return

        # --- Сценарий 1: У нас НЕТ открытой позиции по этому инструменту ---
        if not position:
            # Логика входа в новую позицию
            if event.direction == "BUY":
                # В текущей версии количество лотов = 1.
                # ToDo: Расчет размера позиции. Здесь?
                order = OrderEvent(figi=figi, quantity=1, direction="BUY")
                # Кладем приказ в очередь
                self.events_queue.put(order)
                # Добавляем FIGI в список ордеров на исполнение
                self.pending_orders.add(figi)
                logging.info(f"Портфель генерирует ордер на ПОКУПКУ {figi}")
            # ToDo: Сделать шорт event.direction == 'SELL'
        # --- Сценарий 2: У нас ЕСТЬ открытая позиция по этому инструменту---
        else:
            # Логика выхода из существующей позиции (лонга)
            # Работает !ПО СИГНАЛУ И ТОЛЬКО ПО СИГНАЛУ!
            # (если есть купленный лот, а сигнал на продажу)
            # Закрытие по SL/TP в update_market_price
            if event.direction == "SELL" and position['direction'] == 'BUY':
                # Создаем приказ на продажу того же количества, что и в позиции

                # !!Можно и побольше написать, чтобы не только продал, но и закупил для разворота!!
                # !!Это уже к стратегии вопросы!!
                # Стратегия могла бы сгенерировать два события подряд:
                # сначала SignalEvent(direction="SELL") для закрытия,
                # а потом SignalEvent(direction="SELL") для открытия шорта.
                # И оба в очередь-конвейер
                # Портфель бы их последовательно обработал.

                order = OrderEvent(figi=figi, quantity=position['quantity'], direction="SELL")
                # Кладем приказ в очередь
                self.events_queue.put(order)
                # Также помечаем ордер на закрытие как ожидающий
                self.pending_orders.add(figi)
                logging.info(f"Портфель генерирует ордер на ПРОДАЖУ (закрытие) {figi}")
            # ToDo: Сделать выход из шорта event.direction == 'Buy' and position['direction'] == 'SELL'
            # ToDo: Можно и докупать если была открыта позиция на Buy и пришел опять Buy. Нужно ли?

    def on_fill(self, event: FillEvent):
        """
        Выполняется после фактического исполнения ордера (FillEvent).
        Обновляются позиции и капитал. Сохраняется инфо для отчета.
        """
        figi = event.figi
        
        # Убираем ордер из списка ожидающих, если он там был
        # Если пришел FillEvent, то ордер исполнен
        if figi in self.pending_orders:
            self.pending_orders.remove(figi)

        # Получаем последнюю известную свечу, чтобы определить цену исполнения
        last_candle = self.last_market_data.get(figi)

        # На всякий случай эта проверка. Но я честно не понимаю зачем. Только как защита от ошибок при рефакторинге
        # Такие пояснения еще нравятся:
        # on_fill не должен полагаться на неявное знание о том, что
        # update_market_price всегда вызывается раньше. Он должен быть самодостаточным.
        # А также
        # Этот блок кода явно декларирует: "Для работы этого метода необходимо,
        # чтобы last_market_data содержал данные по этому figi". Это самодокументируемый код.

        if last_candle is None:
            logging.error(f"Нет рыночных данных для исполнения ордера по {figi}")
            return
        
        position = self.current_positions.get(figi)
        
        # --- Сценарий 1: Открытие НОВОЙ позиции ---
        if not position: # Так как в position нет текущего figi
            # Допущение, что цена исполнения равна цене открытия свечи, на которой пришел сигнал.
            # ToDo: нет проскальзывания и т.п. Хоть бы рандом прикрутить на десятую долю процента
            execution_price = last_candle['open']
            # Получаем параметры риска из объекта стратегии и переводим их из процентов в доли.
            sl_percent = self.strategy.stop_loss_percent / 100.0
            tp_percent = self.strategy.take_profit_percent / 100.0

            # Рассчитываем абсолютные уровни цен для SL и TP
            if event.direction == 'BUY':
                stop_loss_price = execution_price * (1 - sl_percent)
                take_profit_price = execution_price * (1 + tp_percent)
            # ToDo: для шорта
            else:
                stop_loss_price = execution_price * (1 + sl_percent)
                take_profit_price = execution_price * (1 - tp_percent)

            # Создаем запись о новой позиции в нашем "журнале активных сделок"
            self.current_positions[figi] = {
                'quantity': event.quantity, 'entry_price': execution_price,
                'direction': event.direction, 'stop_loss': stop_loss_price,
                'take_profit': take_profit_price, 'exit_reason': None
            }
            logging.info(f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {figi} @ {execution_price:.2f} | SL: {stop_loss_price:.2f}, TP: {take_profit_price:.2f}")

        # --- Сценарий 2: Закрытие СУЩЕСТВУЮЩЕЙ позиции ---
        # Так как позиция есть (не прошла if выше) и её направление
        # противоположно направлению исполненного ордера (FillEvent).
        #
        # Да, проверка лишняя (можно просто else), но она добавляет наглядности,
        # так как если направление позиции совпадает с сигналом,
        # то on_signal ордер не откроет.
        elif event.direction != position['direction']:

            entry_price = position['entry_price']

            # ToDo: нет проскальзывания и т.п. Хоть бы рандом прикрутить на десятую долю процента
            # Определяем цену в зависимости от причины выхода
            if position.get('exit_reason') == "Stop Loss":
                # Если позиция закрывается по стопу, цена выхода равна уровню стопа
                exit_price = position['stop_loss']
                exit_reason = "Stop Loss"
            elif position.get('exit_reason') == "Take Profit":
                # Если по тейку - то цена равна уровню тейка
                exit_price = position['take_profit']
                exit_reason = "Take Profit"
            else: # Закрытие по сигналу от стратегии
                # Если причина не указана, значит, это закрытие по сигналу от стратегии.
                # Цена выхода - цена открытия текущей свечи.
                exit_price = last_candle['open']
                exit_reason = "Signal"

            # Рассчитываем комиссию за вход и выход
            commission = (entry_price * event.quantity + exit_price * event.quantity) * self.commission_rate
            
            # Рассчитываем PnL
            if position['direction'] == 'BUY':
                pnl = (exit_price - entry_price) * event.quantity - commission
            else: # ToDo: для шорта
                pnl = (entry_price - exit_price) * event.quantity - commission

            # Обновляем текущий капитал
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