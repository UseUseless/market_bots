from queue import Queue
import logging

from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent
from utils.trade_logger import log_trade
from strategies.base_strategy import BaseStrategy
from config import BACKTEST_CONFIG, RISK_CONFIG
from core.sizer import FixedRiskSizer
from core.risk_manager import BaseRiskManager, FixedRiskManager, AtrRiskManager

class Portfolio:
    """
    Управляет состоянием счета, позициями и генерирует ордера.
    Выступает в роли риск-менеджера и логического центра программы, управляющего событиями.
    """
    def __init__(self, events_queue: Queue, trade_log_file: str, strategy: BaseStrategy,
                 initial_capital: float, commission_rate: float, backtest_params: dict):
        self.events_queue = events_queue        # Ссылка на общую очередь событий для отправки ордеров
        self.trade_log_file = trade_log_file    # Путь к CSV-файлу для записи сделок
        self.strategy = strategy                # Экземпляр текущей стратегии (нужен для доступа к SL/TP)
        self.initial_capital = initial_capital  # Начальный капитал для бэктеста
        self.current_capital = initial_capital  # Текущий капитал, изменяется после каждой сделки
        self.commission_rate = commission_rate  # Размер комиссии в долях Ex:(0.0005 = 0.05%)
        self.position_sizer = FixedRiskSizer()
        self.backtest_params = backtest_params
        self.risk_manager_type = self.backtest_params["risk_manager"]
        self.risk_manager: BaseRiskManager | None = None

        self.slippage_config = BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {"ENABLED": False})
        self.slippage_enabled = self.slippage_config.get("ENABLED", False)
        self.impact_coefficient = self.slippage_config.get("IMPACT_COEFFICIENT", 0.1)

        # Словарь для хранения всех активных позиций. Ключ - FIGI, значение - словарь с деталями позиции.
        self.current_positions = {}
        # Set для хранения FIGI, по которым был отправлен ордер, но еще не пришел отчет об исполнении (FillEvent).
        self.pending_orders = set()
        # Информация о всех закрытых сделках для финального отчета.
        self.closed_trades = []
        # Словарь для хранения последней полученной свечи по каждому инструменту.
        # Нужно для определения цены исполнения ордера (покупка, продажа)
        self.last_market_data = {}

    def _simulate_slippage(self, ideal_price: float, quantity: int, direction: str, candle_volume: int) -> float:
        """
        Симулирует проскальзывание цены исполнения на основе объема.
        """
        # Если модель проскальзывания отключена или объем на свече нулевой, возвращаем идеальную цену.
        if not self.slippage_enabled or candle_volume == 0:
            return ideal_price

        # Рассчитываем долю нашей сделки в общем объеме свечи
        volume_ratio = quantity / candle_volume

        # Модель "квадратного корня" (Проскальзывание = Coeff * (Объем_сделки / Объем_на_свече) ^ 0.5)
        slippage_percent = self.impact_coefficient * (volume_ratio ** 0.5)

        # Применяем проскальзывание. Цена всегда двигается "против нас".
        if direction == 'BUY':
            # При покупке цена становится ВЫШЕ
            return ideal_price * (1 + slippage_percent)
        else:  # Для SELL
            # При продаже цена становится НИЖЕ
            return ideal_price * (1 - slippage_percent)

    def _get_risk_manager(self, last_candle) -> BaseRiskManager | None:
        """Фабрика, создающая нужный экземпляр риск-менеджера."""
        if self.risk_manager_type == "FIXED":
            return FixedRiskManager()
        elif self.risk_manager_type == "ATR":
            atr_period = RISK_CONFIG["ATR_PERIOD"]
            atr_value = last_candle.get(f'ATR_{atr_period}')
            if atr_value:
                return AtrRiskManager(atr_value=atr_value)
        return None

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

        # Проверяем условия SL/TP для ЛОНГА
        if position['direction'] == 'BUY':
            # Если минимальная цена свечи коснулась или пробила наш стоп-лосс
            if candle_low <= position['stop_loss']:
                exit_reason = "Stop Loss"
                exit_direction = "SELL"
            # Если максимальная цена свечи коснулась или пробила наш тейк-профит
            elif candle_high >= position['take_profit']:
                exit_reason = "Take Profit"
                exit_direction = "SELL"

        # Проверяем условия SL/TP для ШОРТА
        elif position['direction'] == 'SELL':
            # Если максимальная цена свечи коснулась или пробила наш стоп-лосс
            if candle_high >= position['stop_loss']:
                exit_reason = "Stop Loss"
                exit_direction = "BUY"  # Направление ордера для закрытия
            # Если минимальная цена свечи коснулась или пробила наш тейк-профит
            elif candle_low <= position['take_profit']:
                exit_reason = "Take Profit"
                exit_direction = "BUY"

        # Логика выхода из существующей позиции !ПО СТОП_ЛОСС ИЛИ ТЕЙК_ПРОФИТ И ТОЛЬКО ПО НИМ!
        # Закрытие по сигналу в on_signal
        if exit_reason:
            logging.warning(f"!!! СРАБОТАЛ {exit_reason.upper()} для {figi}. Генерирую ордер на закрытие.")
            # Создаем приказ (OrderEvent) на закрытие позиции
            order = OrderEvent(figi=figi, quantity=position['quantity'], direction=exit_direction)
            # Кладем OrderEvent в общую очередь событий
            self.events_queue.put(order)
            # Добавляем ордер в список ожидающих на исполнение, чтобы его исполнить в первую очередь
            self.pending_orders.add(figi)
            # Помечаем позицию как "ожидающую закрытия", чтобы избежать дублирования ордеров и пишем причину
            self.current_positions[figi]['exit_reason'] = exit_reason

    def on_signal(self, event: SignalEvent):
        """Обрабатывает сигнал от стратегии, рассчитывает размер позиции
        с помощью PositionSizer и решает, отправлять ли ордер."""
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
        if not position: # Логика входа в новую позицию
            # Получаем последнюю свечу, чтобы иметь доступ к цене и ATR
            last_candle = self.last_market_data.get(figi)
            if last_candle is None:
                logging.warning(f"Нет рыночных данных для обработки сигнала по {figi}, сигнал проигнорирован.")
                return

            self.risk_manager = self._get_risk_manager(last_candle)
            if not self.risk_manager:
                logging.error(f"Не удалось создать RiskManager '{self.risk_manager_type}'.")
                return

            ideal_entry_price = last_candle['open']

            # Рассчитываем SL
            stop_loss_price = self.risk_manager.calculate_stop_loss(ideal_entry_price, event.direction)

            # 3. Рассчитываем размер позиции
            quantity = self.position_sizer.calculate_size(
                capital=self.current_capital,
                entry_price=ideal_entry_price,
                stop_loss_price=stop_loss_price,
                direction=event.direction
            )

            # TODO: Для акций нужно округление до целого лота. Для крипты - нет.
            # Пока оставляем округление. Можно вынести в конфиг.
            quantity = int(quantity)

            # Если расчет показал, что можно открыть позицию (quantity > 0)
            if quantity > 0:
                order = OrderEvent(figi=figi, quantity=quantity, direction=event.direction)
                self.events_queue.put(order)
                self.pending_orders.add(figi)
                logging.info(f"Портфель генерирует ордер на {event.direction} {quantity} лот(ов) {figi}")

        # --- Сценарий 2: У нас ЕСТЬ открытая позиция по этому инструменту---
        else:
            # Логика выхода из существующей позиции
            # Работает !ПО СИГНАЛУ И ТОЛЬКО ПО СИГНАЛУ!
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
                logging.info(f"Портфель генерирует ордер на ПРОДАЖУ (закрытие лонга) {figi}")
            elif event.direction == "BUY" and position['direction'] == 'SELL':
                # Создаем приказ на продажу того же количества, что и в позиции
                order = OrderEvent(figi=figi, quantity=position['quantity'], direction="BUY")
                # Кладем приказ в очередь
                self.events_queue.put(order)
                # Также помечаем ордер на закрытие как ожидающий
                self.pending_orders.add(figi)
                logging.info(f"Портфель генерирует ордер на ПОКУПКУ (закрытие шорта) {figi}")
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
            ideal_price = last_candle['open']
            candle_volume = last_candle['volume']

            # Рассчитываем цену с учетом проскальзывания ---
            execution_price = self._simulate_slippage(
                ideal_price=ideal_price,
                quantity=event.quantity,
                direction=event.direction,
                candle_volume=candle_volume
            )

            # Рассчитываем ФИНАЛЬНЫЕ уровни SL и TP здесь на основе реальной цены входа
            self.risk_manager = self._get_risk_manager(last_candle)
            if not self.risk_manager: return

            # 1Считаем финальный стоп-лосс на основе РЕАЛЬНОЙ цены входа
            final_stop_loss = self.risk_manager.calculate_stop_loss(execution_price, event.direction)

            # Считаем финальный тейк-профит
            final_take_profit = self.risk_manager.calculate_take_profit(execution_price, event.direction,
                                                                        final_stop_loss)

            self.current_positions[figi] = {
                'quantity': event.quantity,
                'entry_price': execution_price,
                'direction': event.direction,
                'stop_loss': final_stop_loss,
                'take_profit': final_take_profit,
                'exit_reason': None
            }
            logging.info(f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {figi} @ {execution_price:.2f} | SL: {final_stop_loss:.2f}, TP: {final_take_profit:.2f}")

        # --- Сценарий 2: Закрытие СУЩЕСТВУЮЩЕЙ позиции ---
        # Так как позиция есть (не прошла if выше) и её направление
        # противоположно направлению исполненного ордера (FillEvent).
        #
        # Да, проверка лишняя (можно просто else), но она добавляет наглядности,
        # так как если направление позиции совпадает с сигналом,
        # то on_signal ордер не откроет.
        elif event.direction != position['direction']:

            entry_price = position['entry_price']
            candle_volume = last_candle['volume']

            # Определяем цену в зависимости от причины выхода
            if position.get('exit_reason') == "Stop Loss":
                # Если позиция закрывается по стопу, цена выхода равна уровню стопа
                ideal_exit_price = position['stop_loss']
                exit_reason = "Stop Loss"
            elif position.get('exit_reason') == "Take Profit":
                # Если по тейку - то цена равна уровню тейка
                ideal_exit_price = position['take_profit']
                exit_reason = "Take Profit"
            else: # Закрытие по сигналу от стратегии
                # Если причина не указана, значит, это закрытие по сигналу от стратегии.
                # Цена выхода - цена открытия текущей свечи.
                ideal_exit_price = last_candle['open']
                exit_reason = "Signal"

            # Рассчитываем цену выхода с учетом проскальзывания ---
            exit_price = self._simulate_slippage(
                ideal_price=ideal_exit_price,
                quantity=event.quantity,
                direction=event.direction,
                candle_volume=candle_volume
            )

            # Рассчитываем комиссию за вход и выход
            commission = (entry_price * event.quantity + exit_price * event.quantity) * self.commission_rate
            
            # Рассчитываем PnL
            if position['direction'] == 'BUY': # Для лонга
                pnl = (exit_price - entry_price) * event.quantity - commission
            else: # Для шорта
                pnl = (entry_price - exit_price) * event.quantity - commission

            # Обновляем текущий капитал
            self.current_capital += pnl
            
            # Логируем сделку в CSV
            log_trade(
                trade_log_file=self.trade_log_file,
                strategy_name=self.strategy.name,
                figi=figi,
                direction=position['direction'],
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                exit_reason=exit_reason,
                interval=self.backtest_params["interval"],
                risk_manager=self.backtest_params["risk_manager"]
            )
            # Сохраняем информацию о сделке для финального отчета
            self.closed_trades.append({'pnl': pnl})

            # Окончательно удаляем позицию из списка активных
            del self.current_positions[figi]
            logging.info(f"Позиция ЗАКРЫТА по причине '{exit_reason}': {figi}. PnL: {pnl:.2f}. Капитал: {self.current_capital:.2f}")