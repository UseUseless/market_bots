"""
Модуль основного цикла бэктеста (Backtest Engine).

Этот модуль отвечает за оркестрацию симуляции торговли на исторических данных.
Он связывает поток данных, торговую стратегию, управление портфелем и
симуляцию исполнения ордеров в единый событийный цикл.

Основные функции:
    - Загрузка и подготовка данных (расчет индикаторов).
    - Инициализация компонентов через Dependency Injection.
    - Запуск цикла по свечам (Event Loop).
    - Сбор результатов и формирование DataFrame сделок.
"""

import queue
import logging
from typing import Dict, Any, Optional

import pandas as pd

from app.shared.events import MarketEvent, SignalEvent, OrderEvent, FillEvent
from app.shared.schemas import TradingConfig
from app.shared.logging_setup import backtest_time_filter
from app.shared.config import config as app_config

from app.infrastructure.feeds.backtest.provider import BacktestDataProvider, BacktestDataLoader
from app.infrastructure.storage.file_io import load_instrument_info

from app.core.portfolio import Portfolio
from app.core.engine.backtest.simulator import BacktestExecutionHandler
from app.strategies import AVAILABLE_STRATEGIES

logger = logging.getLogger('backtester')


class BacktestEngine:
    """
    Движок для запуска одиночной симуляции стратегии.

    Управляет процессом прогона истории свеча за свечой, эмулируя поведение
    рынка и исполнение ордеров.

    Attributes:
        config (TradingConfig): Полная конфигурация сессии.
        data_slice (Optional[pd.DataFrame]): Предоставленные данные (для WFO).
        events_queue (queue.Queue): Шина событий.
        components (Dict): Реестр инициализированных компонентов.
    """

    def __init__(self, config: TradingConfig, data_slice: Optional[pd.DataFrame] = None):
        """
        Инициализирует движок.

        Args:
            config: Конфигурация запуска (инструмент, стратегия, риск).
            data_slice: DataFrame с данными. Если None, загружается с диска.
        """
        self.config = config
        self.data_slice = data_slice

        self.events_queue = queue.Queue()
        self.components: Dict[str, Any] = {}
        self.current_candle: Optional[pd.Series] = None

        # Буфер для рыночного ордера стратегии.
        # Сигнал возникает на Close(T), ордер исполняется на Open(T+1).
        self.pending_strategy_order: Optional[OrderEvent] = None

    def _initialize_components(self) -> None:
        """
        Инициализирует и связывает основные компоненты системы (Composition Root).
        """
        logger.info("Инициализация компонентов бэктеста...")

        # 1. Загрузка метаданных инструмента (лотность, шаги цены)
        instrument_info = load_instrument_info(
            self.config.exchange, self.config.instrument, self.config.interval
        )

        # 2. Инициализация Стратегии
        StrategyClass = AVAILABLE_STRATEGIES[self.config.strategy_name]
        strategy = StrategyClass(
            events_queue=self.events_queue,
            config=self.config
        )
        self.components['strategy'] = strategy

        # 3. Инициализация Портфеля (включает Риск-менеджер и Учет)
        portfolio = Portfolio(
            config=self.config,
            events_queue=self.events_queue,
            instrument_info=instrument_info
        )
        self.components['portfolio'] = portfolio

        # 4. Инициализация Симулятора исполнения
        slippage_conf = app_config.BACKTEST_CONFIG.get("SLIPPAGE_CONFIG", {})
        execution_handler = BacktestExecutionHandler(
            events_queue=self.events_queue,
            commission_rate=self.config.commission_rate,
            slippage_config=slippage_conf
        )
        self.components['execution_handler'] = execution_handler

    def _prepare_data(self) -> Optional[pd.DataFrame]:
        """
        Загружает исторические данные и рассчитывает индикаторы.

        Returns:
            pd.DataFrame: Обогащенные данные или None в случае ошибки.
        """
        strategy = self.components['strategy']

        # Если данные переданы извне (например, при Оптимизации)
        if self.data_slice is not None:
            raw_data = self.data_slice
        else:
            # Загрузка с диска
            loader = BacktestDataLoader(
                exchange=self.config.exchange,
                instrument_id=self.config.instrument,
                interval_str=self.config.interval,
                data_path=app_config.PATH_CONFIG["DATA_DIR"]
            )
            raw_data = loader.load()

        if raw_data.empty:
            logger.error(f"Нет данных для {self.config.instrument}")
            return None

        # Векторный расчет индикаторов через стратегию
        enriched_data = strategy.process_data(raw_data.copy())

        if len(enriched_data) < strategy.min_history_needed:
            logger.error("Недостаточно истории после расчета индикаторов.")
            return None

        return enriched_data

    def _process_queue(self):
        """
        Разбирает очередь событий и маршрутизирует их между компонентами.
        """
        portfolio = self.components['portfolio']
        execution = self.components['execution_handler']

        # Используем локальную ссылку для безопасности
        current_candle = self.current_candle

        while not self.events_queue.empty():
            try:
                event = self.events_queue.get(block=False)
            except queue.Empty:
                break

            if isinstance(event, SignalEvent):
                # Стратегия -> Портфель (Запрос на вход/выход)
                portfolio.on_signal(event, current_candle)

            elif isinstance(event, OrderEvent):
                # Портфель -> Симулятор (Ордер на исполнение)
                if event.price is None:
                    # Market Order от стратегии: исполняем на следующей свече
                    self.pending_strategy_order = event
                else:
                    # Limit/Stop (SL/TP): проверяем исполнение внутри текущей свечи
                    execution.execute_order(event, current_candle)

            elif isinstance(event, FillEvent):
                # Симулятор -> Портфель (Подтверждение сделки)
                portfolio.on_fill(event)

    def run(self) -> Dict[str, Any]:
        """
        Запускает основной цикл симуляции.

        Returns:
            Dict[str, Any]: Результаты теста (статус, сделки, капитал).
        """
        try:
            self._initialize_components()
            data = self._prepare_data()
            if data is None:
                return {"status": "error", "message": "No data available"}

            feed = BacktestDataProvider(data, self.config.interval)

            portfolio = self.components['portfolio']
            strategy = self.components['strategy']
            execution = self.components['execution_handler']

            # --- Event Loop (Цикл по свечам) ---
            while feed.next():
                self.current_candle = feed.get_current_candle()

                # Создаем событие рынка для обновления оценки портфеля
                market_event = MarketEvent(
                    timestamp=self.current_candle['time'],
                    instrument=self.config.instrument,
                    data=self.current_candle
                )

                # Обновляем время в логгере
                backtest_time_filter.set_sim_time(market_event.timestamp)

                # 1. Фаза исполнения (Open): Исполняем отложенные рыночные ордера
                if self.pending_strategy_order:
                    # Исполнение по цене Open текущей свечи
                    execution.execute_order(self.pending_strategy_order, self.current_candle)
                    self.pending_strategy_order = None
                    self._process_queue()

                # 2. Фаза контроля рисков (High/Low): Проверка SL/TP внутри свечи
                portfolio.on_market_data(market_event)
                self._process_queue()

                # 3. Фаза стратегии (Close): Анализ закрытой свечи
                strategy.on_candle(feed)
                self._process_queue()

            # --- Завершение ---
            backtest_time_filter.reset_sim_time()

            # Конвертация сделок в DataFrame для модуля аналитики
            # Мапим имена полей Trade в формат, ожидаемый AnalysisSession
            trade_dicts = []
            for t in portfolio.closed_trades:
                d = t.__dict__.copy()
                d['entry_timestamp_utc'] = d['entry_time']
                d['exit_timestamp_utc'] = d['exit_time']
                trade_dicts.append(d)

            trades_df = pd.DataFrame(trade_dicts)

            return {
                "status": "success",
                "trades_df": trades_df,
                "final_capital": portfolio.balance,
                "initial_capital": self.config.initial_capital,
                "enriched_data": data
            }

        except Exception as e:
            logger.error(f"Backtest Critical Error: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}