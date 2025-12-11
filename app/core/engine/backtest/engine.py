"""
Модуль основного цикла бэктеста (Backtest Engine).

Этот модуль отвечает за оркестрацию симуляции торговли на исторических данных.
Он связывает поток данных, торговую стратегию, управление портфелем и
симуляцию исполнения ордеров в единый событийный цикл.

Ключевая особенность — строгое соблюдение причинно-следственных связей времени:
сигналы генерируются по закрытию свечи (Close), а исполняются на открытии
следующей (Open), либо внутри свечи (High/Low) для лимитных ордеров.

Симулятор исполнения ордеров (Execution Simulator).

Этот модуль эмулирует работу биржевого движка (Matching Engine) в режиме бэктеста.
Его задача — превратить ордер (намерение) в сделку (факт) с учетом рыночных условий,
ликвидности и транзакционных издержек.

"""

import queue
import logging
from queue import Queue
from typing import Dict, Any, Optional

import pandas as pd

from app.shared.events import MarketEvent, SignalEvent, OrderEvent, FillEvent
from app.shared.types import TradeDirection
from app.shared.schemas import TradingConfig
from app.shared.logging_setup import backtest_time_filter
from app.shared.config import config as app_config

from app.infrastructure.feeds.backtest.provider import BacktestDataProvider, BacktestDataLoader
from app.infrastructure.files.file_io import load_instrument_info

from app.core.portfolio import Portfolio
from app.strategies import AVAILABLE_STRATEGIES

logger = logging.getLogger('backtester')


class BacktestEngine:
    """
    Движок для запуска одиночной симуляции стратегии.

    Управляет процессом прогона истории свеча за свечой (Bar-by-Bar), эмулируя
    поведение рынка и взаимодействие компонентов системы.

    Attributes:
        config (TradingConfig): Полная конфигурация сессии (стратегия, инструмент, риск).
        data_slice (Optional[pd.DataFrame]): Предоставленные данные (используется для WFO).
            Если None, движок сам загрузит данные с диска.
        events_queue (queue.Queue): Шина событий для обмена сообщениями между компонентами.
        components (Dict): Реестр инициализированных компонентов (Strategy, Portfolio, etc.).
        current_candle (Optional[pd.Series]): Данные текущей обрабатываемой свечи.
        pending_strategy_order (Optional[OrderEvent]): Буфер для рыночного ордера,
            сгенерированного на прошлой свече и ожидающего исполнения на Open текущей.
    """

    def __init__(self, config: TradingConfig, data_slice: Optional[pd.DataFrame] = None):
        """
        Инициализирует движок бэктеста.

        Args:
            config: Объект конфигурации.
            data_slice: DataFrame с историей (опционально).
        """
        self.config = config
        self.data_slice = data_slice

        self.events_queue = queue.Queue()
        self.components: Dict[str, Any] = {}
        self.current_candle: Optional[pd.Series] = None

        # Буфер для эмуляции задержки исполнения Market-ордеров (Close -> Next Open)
        self.pending_strategy_order: Optional[OrderEvent] = None

    def _initialize_components(self) -> None:
        """
        Инициализирует и связывает основные компоненты системы (Composition Root).

        Создает экземпляры Стратегии, Портфеля и Исполнителя, внедряя в них
        общую очередь событий и конфигурацию.
        """
        logger.info("Инициализация компонентов бэктеста...")

        # 1. Загрузка метаданных инструмента (шаг цены, размер лота)
        # Это необходимо для корректного округления объемов в портфеле.
        instrument_info = load_instrument_info(
            self.config.exchange, self.config.instrument, self.config.interval
        )

        # 2. Инициализация Стратегии (Factory Pattern через реестр)
        StrategyClass = AVAILABLE_STRATEGIES[self.config.strategy_name]
        strategy = StrategyClass(
            events_queue=self.events_queue,
            config=self.config
        )
        self.components['strategy'] = strategy

        # 3. Инициализация Портфеля (включает Риск-менеджер и Учет позиций)
        portfolio = Portfolio(
            config=self.config,
            events_queue=self.events_queue,
            instrument_info=instrument_info
        )
        self.components['portfolio'] = portfolio

        # 4. Инициализация Симулятора исполнения (Slippage & Commission)
        slippage_conf = app_config.BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {})
        execution_handler = BacktestExecutionHandler(
            events_queue=self.events_queue,
            commission_rate=self.config.commission_rate,
            slippage_config=slippage_conf
        )
        self.components['execution_handler'] = execution_handler

    def _prepare_data(self) -> Optional[pd.DataFrame]:
        """
        Подготавливает данные для симуляции.

        1. Загружает сырые данные (с диска или из памяти).
        2. Запускает векторный расчет индикаторов через стратегию.
        3. Проверяет достаточность истории для разогрева (Warm-up).

        Returns:
            pd.DataFrame: Обогащенные данные с индикаторами, или None при ошибке.
        """
        strategy = self.components['strategy']

        # Определение источника данных (Память для WFO или Диск для Single Run)
        if self.data_slice is not None:
            raw_data = self.data_slice
        else:
            loader = BacktestDataLoader(
                exchange=self.config.exchange,
                instrument_id=self.config.instrument,
                interval_str=self.config.interval,
                data_path=app_config.PATH_CONFIG["DATA_DIR"]
            )
            raw_data = loader.load_raw_data()

        if raw_data.empty:
            logger.error(f"Нет данных для {self.config.instrument}")
            return None

        # Расчет индикаторов (Vectorized Calculation)
        # Стратегия получает весь DF и добавляет колонки (SMA, RSI и т.д.) разом.
        enriched_data = strategy.process_data(raw_data.copy())

        # Проверка на минимальный размер истории
        if len(enriched_data) < strategy.min_history_needed:
            logger.error("Недостаточно истории после расчета индикаторов (Warm-up period).")
            return None

        return enriched_data

    def _process_queue(self):
        """
        Маршрутизатор событий (Event Dispatcher).

        Разбирает очередь событий и направляет их соответствующим компонентам.
        Работает синхронно, опустошая очередь на каждом шаге цикла.
        """
        portfolio = self.components['portfolio']
        execution = self.components['execution_handler']

        # Локальная ссылка для скорости и безопасности
        current_candle = self.current_candle

        while not self.events_queue.empty():
            try:
                event = self.events_queue.get(block=False)
            except queue.Empty:
                break

            # Роутинг событий
            if isinstance(event, SignalEvent):
                # Стратегия -> Портфель: Запрос на вход/выход
                portfolio.on_signal(event, current_candle)

            elif isinstance(event, OrderEvent):
                # Портфель -> Симулятор: Ордер на исполнение
                if event.price is None:
                    # Market Order: Откладываем до следующей свечи (Open execution)
                    # Так как мы не знаем цену открытия следующей свечи сейчас.
                    self.pending_strategy_order = event
                else:
                    # Limit/Stop (SL/TP): Проверяем исполнение немедленно внутри текущей свечи
                    execution.execute_order(event, current_candle)

            elif isinstance(event, FillEvent):
                # Симулятор -> Портфель: Подтверждение сделки, обновление баланса
                portfolio.on_fill(event)

    def run(self) -> Dict[str, Any]:
        """
        Запускает основной цикл симуляции (Main Loop).

        Итерируется по историческим свечам, последовательно вызывая компоненты
        в строгом порядке для имитации рыночной механики.

        Returns:
            Dict[str, Any]: Словарь с результатами (статус, DataFrame сделок, капитал).
        """
        try:
            self._initialize_components()
            data = self._prepare_data()

            if data is None:
                return {"status": "error", "message": "No data available"}

            # Инициализация итератора данных (Feed)
            feed = BacktestDataProvider(data, self.config.interval)

            portfolio = self.components['portfolio']
            strategy = self.components['strategy']
            execution = self.components['execution_handler']

            # --- Event Loop (Цикл по свечам) ---
            while feed.next():
                self.current_candle = feed.get_current_candle()

                # Обновляем время в логгере для корректной отладки
                market_event = MarketEvent(
                    timestamp=self.current_candle['time'],
                    instrument=self.config.instrument,
                    data=self.current_candle
                )
                backtest_time_filter.set_sim_time(market_event.timestamp)

                # === PHASE 1: EXECUTION (Open) ===
                # Исполняем рыночные ордера, сгенерированные на закрытии ПРЕДЫДУЩЕЙ свечи.
                # Цена исполнения = Open текущей свечи.
                if self.pending_strategy_order:
                    execution.execute_order(self.pending_strategy_order, self.current_candle)
                    self.pending_strategy_order = None
                    self._process_queue()

                # === PHASE 2: RISK MONITORING (High/Low) ===
                # Проверяем, были ли задеты уровни SL/TP внутри текущей свечи.
                # Portfolio использует High и Low для проверки пересечения уровней.
                portfolio.on_market_data(market_event)
                self._process_queue()

                # === PHASE 3: STRATEGY (Close) ===
                # Свеча закрылась. Стратегия анализирует данные и генерирует сигналы.
                # Эти сигналы станут ордерами, которые исполнятся в Phase 1 следующей итерации.
                strategy.on_candle(feed)
                self._process_queue()

            # --- Teardown (Завершение) ---
            backtest_time_filter.reset_sim_time()

            # Сбор результатов: конвертация объектов Trade в DataFrame
            trade_dicts = []
            for t in portfolio.closed_trades:
                d = t.__dict__.copy()
                # Маппинг полей для совместимости с AnalysisSession
                d['entry_timestamp_utc'] = d['entry_time']
                d['exit_timestamp_utc'] = d['exit_time']
                trade_dicts.append(d)

            trades_df = pd.DataFrame(trade_dicts)

            return {
                "status": "success",
                "trades_df": trades_df,
                "final_capital": portfolio.balance,
                "initial_capital": self.config.initial_capital,
                "enriched_data": data  # Возвращаем данные для отрисовки графиков
            }

        except Exception as e:
            logger.error(f"Backtest Critical Error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}


class BacktestExecutionHandler:
    """
    Обработчик исполнения ордеров для симуляции.

    Принимает OrderEvent, рассчитывает итоговую цену исполнения (с учетом проскальзывания),
    вычисляет комиссию и генерирует FillEvent.

    Attributes:
        events_queue (Queue): Очередь для отправки событий исполнения (FillEvent).
        commission_rate (float): Ставка комиссии (в долях, например 0.001 = 0.1%).
        slippage_enabled (bool): Флаг включения симуляции проскальзывания.
        impact_coefficient (float): Коэффициент влияния на рынок (Market Impact).
    """

    def __init__(self, events_queue: Queue, commission_rate: float, slippage_config: Dict[str, Any]):
        """
        Инициализирует симулятор.

        Args:
            events_queue: Очередь событий.
            commission_rate: Комиссия биржи за сделку (Taker fee).
            slippage_config: Словарь настроек {'ENABLED': bool, 'IMPACT_COEFFICIENT': float}.
        """
        self.events_queue = events_queue
        self.commission_rate = commission_rate
        self.slippage_enabled = slippage_config.get("ENABLED", False)
        self.impact_coefficient = slippage_config.get("IMPACT_COEFFICIENT", 0.1)

    def _simulate_slippage(self, ideal_price: float, quantity: float,
                           direction: TradeDirection, candle_volume: float) -> float:
        """
        Рассчитывает цену исполнения с учетом влияния объема ордера на рынок (Market Impact).

        Использует упрощенную модель "Square Root Law":
        Slippage % ~ ImpactCoef * sqrt(OrderSize / CandleVolume).

        Это означает, что проскальзывание растет нелинейно: маленькие ордера исполняются
        почти по рынку, а крупные могут существенно сдвинуть цену.

        Args:
            ideal_price: Базовая цена (Open свечи или Limit цена).
            quantity: Объем ордера.
            direction: Направление (BUY/SELL).
            candle_volume: Объем торгов в текущей свече (ликвидность).

        Returns:
            float: Скорректированная цена исполнения.
        """
        if not self.slippage_enabled or candle_volume <= 0:
            return ideal_price

        # Доля ордера в объеме свечи (ограничена 100%, чтобы не ломать математику)
        volume_ratio = min(quantity / candle_volume, 1.0)

        # Расчет процента сдвига цены
        slippage_percent = self.impact_coefficient * (volume_ratio ** 0.5)

        # Жесткое ограничение (Safety Guard): проскальзывание не более 20%
        # Это защищает от аномалий в исторических данных (например, ошибочный нулевой объем)
        slippage_percent = min(slippage_percent, 0.20)

        # Применение сдвига: Покупка дороже, Продажа дешевле
        if direction == TradeDirection.BUY:
            return ideal_price * (1 + slippage_percent)
        else:
            return ideal_price * (1 - slippage_percent)

    def execute_order(self, order: OrderEvent, last_candle: pd.Series):
        """
        Исполняет ордер по рыночным данным переданной свечи.

        Алгоритм:
        1. Определяет базовую цену.
           - Для Market Order: используется цена Open текущей свечи (так как решение принято на Close предыдущей).
           - Для Limit/Stop: используется цена из ордера.
        2. Рассчитывает проскальзывание на основе объема свечи.
        3. Рассчитывает комиссию в валюте котировки.
        4. Генерирует событие исполнения (FillEvent).

        Args:
            order: Событие ордера.
            last_candle: Данные свечи, на которой происходит исполнение.
        """
        if last_candle is None:
            return

        # 1. Определение базовой цены
        if order.price is not None:
            # Лимитный/Стоп ордер: исполняем строго по цене триггера (или хуже, если гэп)
            # Упрощение: считаем, что исполнили по заявленной цене
            base_price = order.price
        else:
            # Рыночный ордер: исполняем по цене открытия свечи
            base_price = float(last_candle['open'])

        # 2. Симуляция проскальзывания
        # Используем объем свечи как прокси ликвидности
        exec_price = self._simulate_slippage(
            ideal_price=base_price,
            quantity=order.quantity,
            direction=order.direction,
            candle_volume=float(last_candle.get('volume', 1000000))
        )

        # 3. Расчет комиссии (Cost = Price * Qty * Rate)
        commission = exec_price * order.quantity * self.commission_rate

        # 4. Генерация события исполнения
        fill = FillEvent(
            timestamp=order.timestamp,
            instrument=order.instrument,
            direction=order.direction,
            quantity=order.quantity,
            price=exec_price,
            commission=commission,
            trigger_reason=order.trigger_reason,
            # Пробрасываем параметры риска для портфеля
            stop_loss=order.stop_loss,
            take_profit=order.take_profit
        )

        self.events_queue.put(fill)
