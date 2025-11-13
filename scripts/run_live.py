import asyncio
import logging
import argparse
from typing import Type, Any
import os

from asyncio import Queue as AsyncQueue

from app.utils.logging_setup import setup_global_logging
from app.utils.clients.tinkoff import TinkoffHandler
from app.utils.clients.bybit import BybitHandler
from app.core.data.stream_handlers import TinkoffStreamDataHandler, BybitStreamDataHandler
from app.core.execution.live import LiveExecutionHandler
from app.core.models.event import Event, MarketEvent, SignalEvent, OrderEvent, FillEvent
from app.core.portfolio import Portfolio
from app.strategies.base_strategy import BaseStrategy

from app.strategies import AVAILABLE_STRATEGIES
from app.core.risk.risk_manager import AVAILABLE_RISK_MANAGERS

async def main_event_loop(events_queue: AsyncQueue, portfolio: Portfolio, strategy: BaseStrategy, execution_handler: Any):
    logging.info("Основной цикл обработки событий запущен...")
    while True:
        event = await events_queue.get()
        if isinstance(event, MarketEvent):
            candle_info = (f"Новая свеча: Time={event.data['time'].strftime('%H:%M:%S')}, "
                           f"O={event.data['open']:.2f}, "
                           f"H={event.data['high']:.2f}, "
                           f"L={event.data['low']:.2f}, "
                           f"C={event.data['close']:.2f}, "
                           f"V={event.data['volume']}")
            logging.info(candle_info)
            portfolio.update_market_price(event)
            strategy.on_market_event(event)
        elif isinstance(event, SignalEvent):
            portfolio.on_signal(event)
        elif isinstance(event, OrderEvent):
            await execution_handler.execute_order(event)
        elif isinstance(event, FillEvent):
            portfolio.on_fill(event)
        events_queue.task_done()

async def run_sandbox(
        exchange: str, instrument: str, interval: str,
        strategy_class: Type[BaseStrategy], risk_manager_type: str, category: str,
        strategy_params: dict, rm_params: dict
):
    events_queue: AsyncQueue[Event] = AsyncQueue()
    loop = asyncio.get_running_loop()
    class AsyncQueuePutter:
        def __init__(self, async_queue: AsyncQueue):
            self._async_queue = async_queue
            self._loop = loop
        def put(self, item):
            asyncio.run_coroutine_threadsafe(self._async_queue.put(item), self._loop)

    sync_compatible_queue = AsyncQueuePutter(events_queue)

    if exchange == 'tinkoff':
        data_client = TinkoffHandler()
    elif exchange == 'bybit':
        data_client = BybitHandler()
    else:
        logging.error(f"Неизвестная биржа: {exchange}")
        return

    logging.info("Получение информации об инструменте...")

    instrument_info = await asyncio.to_thread(data_client.get_instrument_info, instrument, category=category)

    if not instrument_info:
        logging.error(f"Не удалось получить информацию об инструменте {instrument}. Завершение работы.")
        return

    logging.info(f"Информация об инструменте: {instrument_info}")

    if exchange == 'tinkoff':
        data_handler = TinkoffStreamDataHandler(events_queue, instrument, interval)
    elif exchange == 'bybit':
        data_handler = BybitStreamDataHandler(events_queue, instrument, interval, loop, channel_type=category, testnet=True)

    execution_handler = LiveExecutionHandler(events_queue, exchange, trade_mode="SANDBOX", loop=loop)
    strategy = strategy_class(sync_compatible_queue, instrument,
                              params=strategy_params,
                              risk_manager_type=risk_manager_type,
                              risk_manager_params=rm_params)
    from app.core.risk.sizer import FixedRiskSizer
    from app.core.services.risk_monitor import RiskMonitor
    from app.core.services.order_manager import OrderManager
    from app.core.services.fill_processor import FillProcessor

    # 1. Инициализация компонентов ядра
    rm_class = AVAILABLE_RISK_MANAGERS[risk_manager_type]
    risk_manager = rm_class(params=rm_params)
    position_sizer = FixedRiskSizer()

    # 2. Инициализация сервисов
    risk_monitor = RiskMonitor(sync_compatible_queue)
    order_manager = OrderManager(sync_compatible_queue, risk_manager, position_sizer, instrument_info)
    fill_processor = FillProcessor(
        trade_log_file=os.path.join("logs", "live", f"sandbox_{instrument}.jsonl"),
        strategy=strategy,
        risk_manager=risk_manager,
        exchange=exchange,
        interval=interval
    )

    # 3. Сборка "Легкого" Portfolio
    portfolio = Portfolio(
        events_queue=sync_compatible_queue,
        initial_capital=100000.0,  # Хрень написана
        risk_monitor=risk_monitor,
        order_manager=order_manager,
        fill_processor=fill_processor
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
    setup_global_logging()
    parser = argparse.ArgumentParser(description="Запуск торгового бота в режиме песочницы.")
    parser.add_argument("--exchange", type=str, required=True, choices=['tinkoff', 'bybit'])
    parser.add_argument("--instrument", type=str, required=True)
    parser.add_argument("--interval", type=str, default="1min")
    parser.add_argument("--category", type=str, default="linear", help="Категория рынка для Bybit (spot, linear, inverse). По умолчанию: linear")
    parser.add_argument("--strategy", type=str, required=True, help=f"Имя стратегии. Доступно: {list(AVAILABLE_STRATEGIES.keys())}")
    valid_rms = list(AVAILABLE_RISK_MANAGERS.keys())
    parser.add_argument("--rm", dest="risk_manager_type", type=str, default="FIXED", choices=valid_rms, help="Модель управления риском. По умолчанию: FIXED")
    args = parser.parse_args()

    if args.strategy not in AVAILABLE_STRATEGIES:
        print(f"Ошибка: Стратегия '{args.strategy}' не найдена.")
        return

    strategy_class = AVAILABLE_STRATEGIES[args.strategy]
    rm_class = AVAILABLE_RISK_MANAGERS[args.risk_manager_type]
    final_strategy_params = strategy_class.get_default_params()
    final_rm_params = rm_class.get_default_params()

    try:
        asyncio.run(run_sandbox(
            exchange=args.exchange, instrument=args.instrument, interval=args.interval,
            category=args.category, strategy_class=strategy_class,
            risk_manager_type=args.risk_manager_type,
            strategy_params=final_strategy_params, rm_params=final_rm_params
        ))
    except KeyboardInterrupt:
        logging.info("Программа остановлена пользователем.")
    finally:
        asyncio.run(asyncio.sleep(1))

if __name__ == "__main__":
    main()