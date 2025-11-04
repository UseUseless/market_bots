import asyncio
import logging
import pandas as pd
import argparse
from typing import Type, Any, get_args

# --- Асинхронные и синхронные компоненты ---
from asyncio import Queue as AsyncQueue

# --- Импорты компонентов ---
from core.stream_data_handler import TinkoffStreamDataHandler, BybitStreamDataHandler, BaseStreamDataHandler
from core.stream_execution import LiveExecutionHandler
from core.event import Event, MarketEvent, SignalEvent, OrderEvent, FillEvent
from core.portfolio import Portfolio
from strategies.base_strategy import BaseStrategy
from core.risk_manager import RiskManagerType

# --- Импортируем словарь стратегий из run.py ---
from run import AVAILABLE_STRATEGIES


# --- Основной асинхронный движок (без изменений) ---

async def main_event_loop(
        events_queue: AsyncQueue,
        portfolio: Portfolio,
        strategy: BaseStrategy,
        execution_handler: Any
):
    """Главный асинхронный цикл обработки событий."""
    # ... (код этой функции не меняется)
    logging.info("Основной цикл обработки событий запущен...")
    schema = {
        "time": "datetime64[ns, UTC]", "open": "float64", "high": "float64",
        "low": "float64", "close": "float64", "volume": "int64"
    }
    history_df = pd.DataFrame({col: pd.Series(dtype=typ) for col, typ in schema.items()})
    min_history_size = strategy.min_history_needed
    logging.info(f"Стратегия требует минимум {min_history_size} свечей для начала работы.")

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
            new_candle = event.data.to_frame().T
            history_df = pd.concat([history_df, new_candle], ignore_index=True)
            history_df = history_df.astype(schema)
            if len(history_df) > min_history_size * 2:
                history_df = history_df.iloc[-(min_history_size * 2):].reset_index(drop=True)
            if len(history_df) < min_history_size:
                logging.debug(f"Накопление данных... {len(history_df)}/{min_history_size} свечей.")
                continue
            enriched_history = strategy.prepare_data(history_df.copy())
            if enriched_history.empty:
                logging.warning("prepare_data вернул пустой DataFrame, пропускаем свечу.")
                continue
            last_enriched_candle = enriched_history.iloc[-1]
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
    """Главная функция запуска live-симуляции в режиме 'песочницы'."""
    # ... (код этой функции не меняется)
    use_testnet = True
    trade_mode = "SANDBOX"
    events_queue: AsyncQueue[Event] = AsyncQueue()
    loop = asyncio.get_running_loop()

    class AsyncQueuePutter:
        def __init__(self, async_queue: AsyncQueue):
            self._async_queue = async_queue
            self._loop = loop

        def put(self, item):
            asyncio.run_coroutine_threadsafe(self._async_queue.put(item), self._loop)

    sync_compatible_queue = AsyncQueuePutter(events_queue)
    data_handler: BaseStreamDataHandler
    if exchange == 'tinkoff':
        data_handler = TinkoffStreamDataHandler(events_queue, instrument, interval)
    elif exchange == 'bybit':
        data_handler = BybitStreamDataHandler(
            events_queue, instrument, interval, loop,
            channel_type=category,
            testnet=use_testnet
        )
    else:
        logging.error(f"Неизвестная биржа: {exchange}")
        return
    execution_handler = LiveExecutionHandler(events_queue, exchange, trade_mode=trade_mode, loop=loop)
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
    data_task = asyncio.create_task(data_handler.stream_data())
    loop_task = asyncio.create_task(main_event_loop(events_queue, portfolio, strategy, execution_handler))
    logging.info("Для остановки нажмите Ctrl+C")
    try:
        await asyncio.gather(data_task, loop_task)
    finally:
        execution_handler.stop()


def main():
    """Парсер аргументов и точка входа."""
    parser = argparse.ArgumentParser(description="Запуск торгового бота в режиме песочницы.")

    # --- ИЗМЕНЕНИЕ: Полноценный argparse, как в run.py ---
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True)
    parser.add_argument("--interval", type=str, default="1min")
    parser.add_argument(
        "--category", type=str, default="linear",
        help="Категория рынка для Bybit (spot, linear, inverse). По умолчанию: linear"
    )
    parser.add_argument(
        "--strategy", type=str, required=True,
        help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}"
    )
    valid_rms = get_args(RiskManagerType)
    parser.add_argument(
        "--rm", dest="risk_manager_type", type=str, default="FIXED", choices=valid_rms,
        help="Модель управления риском. По умолчанию: FIXED"
    )

    args = parser.parse_args()

    if args.strategy not in AVAILABLE_STRATEGIES:
        print(f"Ошибка: Стратегия '{args.strategy}' не найдена.")
        return

    strategy_class = AVAILABLE_STRATEGIES[args.strategy]

    try:
        asyncio.run(run_sandbox(
            exchange=args.exchange,
            instrument=args.instrument,
            interval=args.interval,
            category=args.category,
            strategy_class=strategy_class,
            risk_manager_type=args.risk_manager_type
        ))
    except KeyboardInterrupt:
        logging.info("Программа остановлена пользователем.")
    finally:
        asyncio.run(asyncio.sleep(1))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    main()