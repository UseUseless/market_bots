import asyncio
import logging
import pandas as pd
import argparse
from typing import Dict, Type, Any

# --- Асинхронные и синхронные компоненты ---
import queue  # Оставляем для синхронных классов
from asyncio import Queue as AsyncQueue

from core.stream_data_handler import TinkoffStreamDataHandler, BybitStreamDataHandler, BaseStreamDataHandler
from core.event import Event, MarketEvent, SignalEvent, OrderEvent, FillEvent
from core.portfolio import Portfolio
from strategies.base_strategy import BaseStrategy
from strategies.triple_filter import TripleFilterStrategy


# --- Заглушки для компонентов, которые мы создадим позже ---

class DummyStreamDataHandler:
    """
    Фейковый поставщик данных. Раз в секунду генерирует новую свечу,
    имитируя live-поток.
    """

    def __init__(self, events_queue: AsyncQueue, instrument: str):
        self.events_queue = events_queue
        self.instrument = instrument
        self.current_price = 100.0

    async def stream_data(self):
        """Асинхронная задача, которая генерирует MarketEvent'ы."""
        import pandas as pd
        from datetime import datetime, UTC

        logging.info("DummyStreamDataHandler: Запуск потока фейковых данных...")
        while True:
            await asyncio.sleep(0.1)

            self.current_price += 0.1

            mock_candle = pd.Series({
                "time": datetime.now(UTC), "open": self.current_price - 0.05,
                "high": self.current_price + 0.1, "low": self.current_price - 0.1,
                "close": self.current_price, "volume": 100
            })

            event = MarketEvent(
                timestamp=mock_candle['time'],
                instrument=self.instrument,
                data=mock_candle
            )
            await self.events_queue.put(event)


class DummyExecutionHandler:
    """Фейковый исполнитель ордеров. Просто логирует получение ордера."""

    async def execute_order(self, event):
        logging.info(f"DummyExecutionHandler: Получен ордер {event}, ничего не делаем.")


# --- Основной асинхронный движок ---

async def main_event_loop(
        events_queue: AsyncQueue,
        portfolio: Portfolio,
        strategy: BaseStrategy,
        execution_handler: Any
):
    """Главный асинхронный цикл обработки событий."""
    logging.info("Основной цикл обработки событий запущен...")
    schema = {
        "time": "datetime64[ns, UTC]",
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "volume": "int64"
    }
    history_df = pd.DataFrame({col: pd.Series(dtype=typ) for col, typ in schema.items()})

    # Минимальное количество свечей, необходимое для расчета всех индикаторов
    # Берем с запасом, по самому длинному индикатору (EMA_200)
    min_history_size = 250

    while True:
        event = await events_queue.get()

        if isinstance(event, MarketEvent):
            candle_info = (
                f"Новая свеча: Time={event.data['time'].strftime('%H:%M:%S')}, "
                f"O={event.data['open']:.2f}, H={event.data['high']:.2f}, "
                f"L={event.data['low']:.2f}, C={event.data['close']:.2f}, "
                f"V={event.data['volume']}"
            )
            logging.info(candle_info)

            # 1. Добавляем новую свечу в историю
            new_candle = event.data.to_frame().T
            history_df = pd.concat([history_df, new_candle], ignore_index=True)

            history_df = history_df.astype(schema)

            # 2. Обрезаем историю, чтобы она не росла бесконечно
            if len(history_df) > min_history_size * 2:
                history_df = history_df.iloc[-(min_history_size * 2):].reset_index(drop=True)

            # 3. Проверяем, достаточно ли у нас данных для расчета
            if len(history_df) < min_history_size:
                logging.debug(f"Накопление данных... {len(history_df)}/{min_history_size} свечей.")
                continue  # Пропускаем обработку, пока не накопим достаточно истории

            # 4. Рассчитываем индикаторы на всей текущей истории
            # ВАЖНО: strategy.prepare_data() модифицирует DataFrame "на месте" (inplace)
            # Поэтому мы передаем копию, чтобы не испортить наш history_df
            enriched_history = strategy.prepare_data(history_df.copy())

            if enriched_history.empty:
                logging.warning("prepare_data вернул пустой DataFrame, пропускаем свечу.")
                continue

            # 5. Берем ПОСЛЕДНЮЮ свечу с уже рассчитанными индикаторами
            last_enriched_candle = enriched_history.iloc[-1]

            # 6. Создаем новый MarketEvent с обогащенными данными и передаем его дальше
            enriched_event = MarketEvent(
                timestamp=event.timestamp,
                instrument=event.instrument,
                data=last_enriched_candle
            )

            portfolio.update_market_price(enriched_event)
            strategy.calculate_signals(enriched_event)

        elif isinstance(event, SignalEvent):
            portfolio.on_signal(event)

        elif isinstance(event, OrderEvent):
            await execution_handler.execute_order(event)

        elif isinstance(event, FillEvent):
            portfolio.on_fill(event)

        events_queue.task_done()


async def run_sandbox(
    exchange: str,
    instrument: str,
    interval: str,
    strategy_class: Type[BaseStrategy],
    risk_manager_type: str,
    category: str
):
    """Главная функция запуска live-симуляции."""

    events_queue: AsyncQueue[Event] = AsyncQueue()

    # --- Адаптер для очереди ---
    # Позволяет синхронным классам класть события в асинхронную очередь
    class AsyncQueuePutter:
        def __init__(self, async_queue: AsyncQueue):
            self._async_queue = async_queue
            self._loop = asyncio.get_running_loop()

        def put(self, item):
            asyncio.run_coroutine_threadsafe(self._async_queue.put(item), self._loop)

    loop = asyncio.get_running_loop()

    sync_compatible_queue = AsyncQueuePutter(events_queue)

    # --- Инициализация компонентов ---
    data_handler: BaseStreamDataHandler
    if exchange == 'tinkoff':
        data_handler = TinkoffStreamDataHandler(events_queue, instrument, interval)
    elif exchange == 'bybit':
        data_handler = BybitStreamDataHandler(
            events_queue, instrument, interval, loop, channel_type=category
        )
    else:
        logging.error(f"Неизвестная биржа: {exchange}")
        return

    execution_handler = DummyExecutionHandler()

    strategy = strategy_class(sync_compatible_queue, instrument)
    portfolio = Portfolio(
        events_queue=sync_compatible_queue,
        trade_log_file=f"logs/sandbox_{instrument}.jsonl",
        strategy=strategy,
        initial_capital=100000.0,
        commission_rate=0.0005,
        interval=interval,
        risk_manager_type=risk_manager_type
    )

    logging.info(f"Запуск песочницы для стратегии '{strategy.name}' на инструменте '{instrument}' ({exchange.upper()})")

    # --- Запуск задач ---
    data_task = asyncio.create_task(data_handler.stream_data())
    loop_task = asyncio.create_task(main_event_loop(events_queue, portfolio, strategy, execution_handler))

    logging.info("Для остановки нажмите Ctrl+C")
    await asyncio.gather(data_task, loop_task)


def main():
    """Парсер аргументов и точка входа."""
    parser = argparse.ArgumentParser(description="Запуск торгового бота в режиме песочницы.")
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True)
    parser.add_argument("--interval", type=str, default="1min")
    parser.add_argument("--category", type=str, default="linear",
        help="Категория рынка для Bybit (spot, linear, inverse). По умолчанию: linear"
    )
    # TODO: Добавить выбор стратегии и RM через аргументы

    args = parser.parse_args()

    try:
        asyncio.run(run_sandbox(
            exchange=args.exchange,
            instrument=args.instrument,
            interval=args.interval,
            category=args.category,
            strategy_class=TripleFilterStrategy,
            risk_manager_type="FIXED"
        ))
    except KeyboardInterrupt:
        logging.info("Программа остановлена пользователем.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    main()