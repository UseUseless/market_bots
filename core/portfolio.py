from queue import Queue
import logging
import pandas as pd # Добавлено для type hinting
from typing import Any, Dict

from core.event import MarketEvent, SignalEvent, OrderEvent, FillEvent, Event # Типы события для обмена в очереди
from utils.trade_logger import log_trade
from strategies.base_strategy import BaseStrategy
from config import BACKTEST_CONFIG
from core.sizer import BasePositionSizer, FixedRiskSizer
from core.risk_manager import BaseRiskManager, FixedRiskManager, AtrRiskManager

logger = logging.getLogger('backtester')

class Portfolio:
    """
    1.  **Управление состоянием**: Отслеживает текущий капитал, открытые позиции и ордера в обработке.
    2.  **Управление рисками**: Проверяет условия стоп-лосса и тейк-профита на каждой новой свече.
    3.  **Бухгалтерия**: Рассчитывает PnL по закрытым сделкам и обновляет капитал.
    4.  **Оркестрация**: Принимает события и на основе их генерирует новые при выполнении условия
    """

    def __init__(self, events_queue: Queue, trade_log_file: str | None, strategy: BaseStrategy,
                 exchange: str, initial_capital: float, commission_rate: float, interval: str,
                 risk_manager_type: str, instrument_info: Dict[str, Any],
                 risk_config: Dict[str, Any], strategy_config: Dict[str, Any]):
        self.events_queue: Queue[Event] = events_queue      # Ссылка на общую очередь событий для отправки ордеров
        self.trade_log_file: str | None = trade_log_file    # Путь к CSV-файлу для записи сделок
        self.strategy: BaseStrategy = strategy              # Экземпляр текущей стратегии (нужен для доступа к SL/TP)
        self.initial_capital: float = initial_capital       # Начальный капитал для бэктеста
        self.commission_rate: float = commission_rate       # Размер комиссии в долях Ex:(0.0005 = 0.05%)
        self.interval: str = interval                       # Текущий таймфрейм (например, '5min'). Нужен для логирования.
        self.instrument_info: Dict[str, Any] = instrument_info  # Сохраняем метаданные
        self.exchange: str = exchange                       # Какая биржа

        # Создаем экземпляр калькулятора размера позиции.
        self.position_sizer: BasePositionSizer = FixedRiskSizer()
        # Тип риск-менеджера ('FIXED' или 'ATR'). Нужен для создания экземпляра риск-менеджера и логирования
        self.risk_manager_type: str = risk_manager_type
        self.risk_manager: BaseRiskManager
        if self.risk_manager_type == "FIXED":
            self.risk_manager: BaseRiskManager = FixedRiskManager()
        elif self.risk_manager_type == "ATR":
            self.risk_manager: BaseRiskManager = AtrRiskManager()
        else:
            # Если тип не распознан, система не сможет работать.
            raise ValueError(f"Unknown risk manager type: {risk_manager_type}")

        # Настройки проскальзывания
        self.slippage_config: dict[str, Any] = BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {"ENABLED": False})
        self.slippage_enabled: bool = self.slippage_config.get("ENABLED", False)                # Включено ли проскальзывание
        self.impact_coefficient: float = self.slippage_config.get("IMPACT_COEFFICIENT", 0.1)    # Коэфф влияния проскальзывания

        # Динамические аттрибуты (меняются в ходе бэктеста)
        self.current_capital: float = initial_capital   # Текущий капитал, изменяется после каждой сделки
        # Словарь для хранения всех активных позиций.
        # Ключ - instrument, значение - словарь с деталями позиции (цена входа, SL, TP и т.д.).
        self.current_positions: dict[str, dict[str, Any]] = {}
        # Множество (set) для хранения instrument, по которым был отправлен ордер, но еще не пришел отчет об исполнении (FillEvent).
        # Это критически важный механизм для предотвращения отправки дублирующих ордеров по одному и тому же инструменту.
        self.pending_orders: set[str] = set()
        # Информация о всех закрытых сделках для финального отчета.
        self.closed_trades: list[dict[str, float]] = []
        # Словарь для хранения последней полученной свечи по каждому инструменту.
        # Нужно для определения цены исполнения ордера (покупка, продажа)
        self.last_market_data: dict[str, pd.Series] = {}

    def _simulate_slippage(self, ideal_price: float, quantity: int, direction: str, candle_volume: int) -> float:
        """
        Приватный метод для симуляции проскальзывания (slippage) с защитой от экстремальных значений.
        """
        if not self.slippage_enabled or candle_volume <= 0:
            return ideal_price

        # Добавляем защиту от неадекватного соотношения объемов.
        # Если наш ордер больше всего объема на свече, считаем, что мы "съели" 100% ликвидности.
        # Это предотвращает volume_ratio > 1 и экспоненциальный рост проскальзывания.
        volume_ratio = min(quantity / candle_volume, 1.0)

        slippage_percent = self.impact_coefficient * (volume_ratio ** 0.5)

        # Добавляем "предохранитель" - максимальный размер проскальзывания.
        # Это защищает от любых непредвиденных сценариев. Цена не может измениться более чем на 20%
        # из-за нашей сделки в рамках одной свечи. Это очень консервативное допущение.
        MAX_SLIPPAGE_PERCENT = 0.20 # 20%
        if slippage_percent > MAX_SLIPPAGE_PERCENT:
            slippage_percent = MAX_SLIPPAGE_PERCENT

        if direction == 'BUY':
            return ideal_price * (1 + slippage_percent)
        else:  # Для SELL
            return ideal_price * (1 - slippage_percent)

    def _check_stop_loss_take_profit(self, event: MarketEvent, position: Dict[str, Any]) -> None:
        """
        Проверяет, не сработал ли SL или TP на текущей свече.
        РЕАЛИЗОВАНО "ПРАВИЛО ПЕРВОГО СТОП-ЛОССА": проверка SL имеет приоритет,
        чтобы устранить "заглядывание в будущее" внутри одной свечи.
        """
        # --- Подготовительный блок ---
        # Извлекаем максимальную и минимальную цену свечи для анализа.
        candle_high = event.data['high']
        candle_low = event.data['low']

        # Инициализируем переменные, которые мы определим, если найдем причину для выхода.
        exit_reason = None
        exit_direction = None

        # --- ЭТАП 1: ПРИОРИТЕТНАЯ ПРОВЕРКА СТОП-ЛОССА ---
        # Это первая и самая важная проверка. Мы исходим из консервативного предположения,
        # что если цена коснулась стоп-лосса, сделка закрывается с убытком,
        # даже если позже внутри той же свечи она достигла тейк-профита.

        # Для длинной позиции (BUY) стоп-лосс срабатывает, если минимальная цена свечи (low) опустилась до нашего уровня SL.
        if position['direction'] == 'BUY' and candle_low <= position['stop_loss']:
            exit_reason, exit_direction = "Stop Loss", "SELL"  # Направление выхода - продажа

        # Для короткой позиции (SELL) стоп-лосс срабатывает, если максимальная цена свечи (high) поднялась до нашего уровня SL.
        elif position['direction'] == 'SELL' and candle_high >= position['stop_loss']:
            exit_reason, exit_direction = "Stop Loss", "BUY"  # Направление выхода - покупка

        # --- Блок немедленного реагирования на Стоп-Лосс ---
        # Если на предыдущем шаге мы определили, что стоп-лосс сработал...
        if exit_reason == "Stop Loss":
            # Логируем это важное событие.
            logger.warning(f"!!! СРАБОТАЛ STOP LOSS для {event.instrument}. Генерирую ордер на закрытие.")

            # ...создаем событие-приказ (OrderEvent) на закрытие позиции.
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                quantity=position['quantity'],
                direction=exit_direction
            )
            # Отправляем приказ в общую очередь событий на исполнение.
            self.events_queue.put(order)

            # Обновляем внутреннее состояние:
            # 1. Помечаем, что по этому инструменту есть ордер в обработке, чтобы избежать дублирования.
            self.pending_orders.add(event.instrument)
            # 2. Записываем причину выхода в саму позицию. Это понадобится в on_fill для корректного лога.
            self.current_positions[event.instrument]['exit_reason'] = exit_reason

            # КЛЮЧЕВОЙ МОМЕНТ: Немедленно выходим из метода.
            # Это гарантирует, что проверка на тейк-профит ниже НЕ будет выполнена.
            return

        # --- ЭТАП 2: ПРОВЕРКА ТЕЙК-ПРОФИТА ---
        # Этот код выполнится только в том случае, если стоп-лосс на Этапе 1 НЕ сработал.

        # Для длинной позиции (BUY) тейк-профит срабатывает, если максимальная цена свечи (high) достигла нашего уровня TP.
        if position['direction'] == 'BUY' and candle_high >= position['take_profit']:
            exit_reason, exit_direction = "Take Profit", "SELL"

        # Для короткой позиции (SELL) тейк-профит срабатывает, если минимальная цена свечи (low) достигла нашего уровня TP.
        elif position['direction'] == 'SELL' and candle_low <= position['take_profit']:
            exit_reason, exit_direction = "Take Profit", "BUY"

        # --- Блок реагирования на Тейк-Профит ---
        # Если на предыдущем шаге мы определили, что сработал тейк-профит...
        if exit_reason == "Take Profit":
            # ...выполняем те же действия, что и для стоп-лосса: логируем, создаем ордер и обновляем состояние.
            logger.warning(f"!!! СРАБОТАЛ TAKE PROFIT для {event.instrument}. Генерирую ордер на закрытие.")
            order = OrderEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                quantity=position['quantity'],
                direction=exit_direction
            )
            self.events_queue.put(order)
            self.pending_orders.add(event.instrument)
            self.current_positions[event.instrument]['exit_reason'] = exit_reason

    def _handle_new_position_signal(self, event: SignalEvent) -> None:
        """Обрабатывает сигнал на открытие новой позиции."""
        # Получаем последнюю свечу, чтобы иметь доступ к цене и ATR
        last_candle = self.last_market_data.get(event.instrument)
        if last_candle is None:
            logger.warning(f"Нет рыночных данных для обработки сигнала по {event.instrument}, сигнал проигнорирован.")
            return

        # В качестве "идеальной" цены входа мы берем цену открытия текущей свечи.
        ideal_entry_price = last_candle['open']

        try:
            # Размер позиции (Quantity) зависит от риска на акцию (расстояния до стоп-лосса).
            # Стоп-лосс (Stop-Loss) зависит от фактической цены входа (Execution Price).
            # Фактическая цена входа (Execution Price) зависит от проскальзывания (Slippage).
            # Проскальзывание (Slippage), в нашей модели, зависит от размера позиции (Quantity).

            # Чтобы избавиться от этого замкнутого круга принимаем допущение об идеальной цене входа
            # для расчета размера позиции (по предварительной цене)
            # Считаем риск профиль: стоп-лосс, риск на акцию и как следствие размер позиции
            # При исполнении on_fill посчитаем проскальзывание-реальную цену исполнения на основе размера позиции

            # Считаем риск-профиль
            risk_profile = self.risk_manager.calculate_risk_profile(
                entry_price=ideal_entry_price,
                direction=event.direction,
                capital=self.current_capital,
                last_candle=last_candle
            )

            # Передаем этот профиль в sizer для расчета количества лотов.
            quantity_float = self.position_sizer.calculate_size(risk_profile)

            quantity = self._adjust_quantity_for_rules(quantity_float)

            # Если расчет показал, что мы можем купить хотя бы 1  возможный лот (например 0.001 btc), генерируем ордер.
            if quantity > 0:
                logger.info(
                    f"Расчетное кол-во: {quantity_float:.4f}, скорректировано до {quantity} с учетом правил биржи.")
                order = OrderEvent(timestamp=event.timestamp, instrument=event.instrument, quantity=quantity, direction=event.direction)
                self.events_queue.put(order)
                self.pending_orders.add(event.instrument)
                logger.info(f"Портфель генерирует ордер на {event.direction} {quantity} лот(ов) {event.instrument}")

        except ValueError as e:
            # Ловим ошибку от AtrRiskManager, если, например, ATR некорректен.
            logger.warning(f"Не удалось рассчитать профиль риска для {event.instrument}: {e}. Сигнал проигнорирован.")

    def _adjust_quantity_for_rules(self, quantity_float: float) -> float | int:
        """
        Корректирует рассчитанное количество лотов с учетом правил биржи
        (лотность, шаг количества, минимальный размер ордера).
        """
        lot_rules = self.instrument_info

        qty_step = float(lot_rules.get("qty_step", 1.0))
        lot_size = int(lot_rules.get("lot_size", 1))
        min_order_qty = float(lot_rules.get("min_order_qty", lot_size))

        # 1. Округляем вниз до ближайшего шага количества (qty_step)
        if qty_step > 0:
            adjusted_qty = (quantity_float // qty_step) * qty_step
        else:
            adjusted_qty = quantity_float

        # 2. Для инструментов с лотностью > 1 (акции Tinkoff), округляем до лота
        if lot_size > 1:
            num_lots = adjusted_qty // lot_size
            adjusted_qty = num_lots * lot_size

        # Определяем, сколько знаков после запятой в qty_step
        if '.' in str(qty_step):
            decimal_places = len(str(qty_step).split('.')[1])
            # Округляем до нужного количества знаков
            adjusted_qty = round(adjusted_qty, decimal_places)

        # 3. Проверяем на минимальный размер ордера
        if adjusted_qty < min_order_qty:
            quantity = 0
        else:
            quantity = adjusted_qty

        # Конвертируем в int, если это возможно без потери точности
        if quantity == int(quantity):
            return int(quantity)

        return quantity

    def _handle_exit_position_signal(self, event: SignalEvent, position: Dict[str, Any]) -> None:
        """Обрабатывает сигнал на закрытие существующей позиции."""
        # Логика выхода из существующей позиции
        # Работает !ПО СИГНАЛУ И ТОЛЬКО ПО СИГНАЛУ!
        # Закрытие по SL/TP в update_market_price
        if (event.direction == "SELL" and position['direction'] == 'BUY') or \
                (event.direction == "BUY" and position['direction'] == 'SELL'):
                # Создаем приказ на продажу того же количества, что и в позиции

                # !!Можно и побольше написать, чтобы не только продал, но и закупил для разворота!!
                # !!Это уже к стратегии вопросы!!
                # Стратегия могла бы сгенерировать два события подряд:
                # сначала SignalEvent(direction="SELL") для закрытия,
                # а потом SignalEvent(direction="SELL") для открытия шорта.
                # И оба в очередь-конвейер
                # Портфель бы их последовательно обработал.

                order = OrderEvent(timestamp=event.timestamp, instrument=event.instrument, quantity=position['quantity'], direction=event.direction)
                # Кладем приказ в очередь
                self.events_queue.put(order)
                # Также помечаем ордер на закрытие как ожидающий
                self.pending_orders.add(event.instrument)
                logger.info(f"Портфель генерирует ордер на ЗАКРЫТИЕ позиции по {event.instrument}")
            # ToDo: Можно и докупать если была открыта позиция на Buy и пришел опять Buy. Нужно ли?

    def _handle_fill_open(self, event: FillEvent, last_candle: pd.Series) -> None:
        """Обрабатывает исполнение ордера на открытие позиции."""
        execution_price = self._simulate_slippage(
            ideal_price=last_candle['open'],
            quantity=event.quantity,
            direction=event.direction,
            candle_volume=last_candle['volume']
        )

        final_risk_profile = self.risk_manager.calculate_risk_profile(
            entry_price=execution_price,
            direction=event.direction,
            capital=self.current_capital,
            last_candle=last_candle
        )

        self.current_positions[event.instrument] = {
            'quantity': event.quantity,
            'entry_price': execution_price,
            'entry_timestamp': last_candle['time'],
            'direction': event.direction,
            'stop_loss': final_risk_profile.stop_loss_price,
            'take_profit': final_risk_profile.take_profit_price,
            'exit_reason': None
        }

        # Логируем открытие позиции
        logger.info(
            f"Позиция ОТКРЫТА: {event.direction} {event.quantity} {event.instrument} @ {execution_price:.2f} | "
            f"SL: {final_risk_profile.stop_loss_price:.2f}, TP: {final_risk_profile.take_profit_price:.2f}")

    def _handle_fill_close(self, event: FillEvent, position: Dict[str, Any], last_candle: pd.Series) -> None:
        """Обрабатывает исполнение ордера на закрытие позиции."""

        entry_price = position['entry_price']
        exit_reason = position.get('exit_reason')

        # ИБолее надежная логика определения цены выхода
        if exit_reason == "Stop Loss":
            # Цена выхода при стоп-лоссе - это сам уровень стоп-лосса.
            ideal_exit_price = position['stop_loss']
        elif exit_reason == "Take Profit":
            # Цена выхода при тейк-профите - это сам уровень тейк-профита.
            ideal_exit_price = position['take_profit']
        else:
            # Если причина не SL/TP, значит, это закрытие по сигналу.
            # Цена выхода - цена открытия текущей свечи.
            ideal_exit_price = last_candle['open']
            exit_reason = "Signal" # Устанавливаем причину, если она была None

        # Рассчитываем РЕАЛЬНУЮ цену выхода с учетом проскальзывания.
        exit_price = self._simulate_slippage(
            ideal_price=ideal_exit_price,
            quantity=event.quantity,
            direction=event.direction,
            candle_volume=last_candle['volume']
        )

        # Рассчитываем комиссию за вход и выход
        commission = (entry_price * event.quantity + exit_price * event.quantity) * self.commission_rate

        # Рассчитываем PnL
        if position['direction'] == 'BUY':  # Для лонга
            pnl = (exit_price - entry_price) * event.quantity - commission
        else:  # Для шорта
            pnl = (entry_price - exit_price) * event.quantity - commission

        # Обновляем текущий капитал
        self.current_capital += pnl

        # Логируем сделку
        log_trade(
            trade_log_file=self.trade_log_file,
            strategy_name=self.strategy.name,
            exchange=self.exchange,
            instrument=event.instrument,
            direction=position['direction'],
            entry_timestamp=position['entry_timestamp'],
            exit_timestamp=last_candle['time'],
            entry_price=position['entry_price'],
            exit_price=exit_price,
            pnl=pnl,
            exit_reason=exit_reason,
            interval=self.interval,
            risk_manager=self.risk_manager_type
        )
        self.closed_trades.append({
            'pnl': pnl,
            'entry_timestamp_utc': position['entry_timestamp'],
            'exit_timestamp_utc': last_candle['time']
        })

        del self.current_positions[event.instrument]
        logger.info(
            f"Позиция ЗАКРЫТА по причине '{exit_reason}': {event.instrument}. PnL: {pnl:.2f}. Капитал: {self.current_capital:.2f}")

    def update_market_price(self, event: MarketEvent) -> None:
        """
        Вызывается на КАЖДЫЙ MarketEvent (новую свечу)
        Обновляет рыночные данные
        Проверяет SL/TP на каждой новой свече (если есть позиция).
        """
        # Получаем instrument и данные из события
        self.last_market_data[event.instrument] = event.data

        # Получаем текущую позицию по этому инструменту
        position = self.current_positions.get(event.instrument)
        # Если позиции нет или по этому инструменту есть ордер, то ничего не делаем больше
        if not position or event.instrument in self.pending_orders:
            return
        # Функция вызывается только если есть позиция по какому-то инструменту
        self._check_stop_loss_take_profit(event, position)


    def on_signal(self, event: SignalEvent) -> None:
        """Обрабатывает сигнал от стратегии, рассчитывает размер позиции
        с помощью PositionSizer и решает, отправлять ли ордер."""
        position = self.current_positions.get(event.instrument)

        # Фильтр: Игнорируем сигналы, если ордер по инструменту в обработке.
        # Так как мы могли в очередь добавить через update_market_price OrderEvent
        # Но мы еще не до конца обработали MarketEvent и от него пришел SignalEvent в очередь
        # У нас в очереди в таком порядке два события: OrderEvent, SignalEvent
        # Мы обрабатываем OrderEvent и он превращается в FillEvent и становится вторым
        # У нас в очереди в таком порядке два события: SignalEvent, FillEvent
        # Запускается этот метод и для игнорирования SignalEvent проверяем pending_orders
        # Переменная pending_orders не пуста, так как она всегда заполняется когда создается OrderEvent
        if event.instrument in self.pending_orders:
            return

        # --- Сценарий 1: У нас НЕТ открытой позиции по этому инструменту ---
        if not position: # Логика входа в новую позицию
            self._handle_new_position_signal(event)
        # --- Сценарий 2: У нас ЕСТЬ открытая позиция по этому инструменту---
        else:
            self._handle_exit_position_signal(event, position)

    def on_fill(self, event: FillEvent) -> None:
        """
        Выполняется после фактического исполнения ордера (FillEvent).
        Обновляются позиции и капитал. Сохраняется инфо для отчета.
        """
        # Убираем ордер из списка ожидающих, если он там был
        # Если пришел FillEvent, то ордер исполнен
        if event.instrument in self.pending_orders:
            self.pending_orders.remove(event.instrument)

        # Получаем последнюю известную свечу, чтобы определить цену исполнения
        last_candle = self.last_market_data.get(event.instrument)

        # На всякий случай эта проверка. Но я честно не понимаю зачем. Только как защита от ошибок при рефакторинге
        # Такие пояснения еще нравятся:
        # on_fill не должен полагаться на неявное знание о том, что
        # update_market_price всегда вызывается раньше. Он должен быть самодостаточным.
        # А также
        # Этот блок кода явно декларирует: "Для работы этого метода необходимо,
        # чтобы last_market_data содержал данные по этому instrument". Это самодокументируемый код.

        if last_candle is None:
            logger.error(f"Нет рыночных данных для исполнения ордера по {event.instrument}")
            return
        
        position = self.current_positions.get(event.instrument)
        
        # --- Сценарий 1: Открытие НОВОЙ позиции ---
        if not position: # Так как в position нет текущего instrument
            self._handle_fill_open(event, last_candle)
        # --- Сценарий 2: Закрытие СУЩЕСТВУЮЩЕЙ позиции ---
        else:
            self._handle_fill_close(event, position, last_candle)
