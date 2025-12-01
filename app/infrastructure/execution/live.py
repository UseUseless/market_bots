"""
Исполнитель ордеров в реальном времени (Live Execution Handler).

Этот модуль отвечает за отправку торговых приказов на биржу (через API)
и получение подтверждений об исполнении (через стримы/WebSocket).
"""

import asyncio
import logging
from asyncio import Queue as AsyncQueue
from datetime import datetime, timezone
from typing import Optional, Dict

import pandas as pd
from tinkoff.invest import AsyncClient, OrderExecutionReportStatus, OrderDirection

from app.shared.events import OrderEvent, FillEvent
from app.core.interfaces import BaseExecutionHandler, BaseTradeClient
from app.infrastructure.exchanges.bybit import BybitHandler
from app.infrastructure.exchanges.tinkoff import TinkoffHandler
from app.shared.primitives import TradeDirection, TriggerReason, ExchangeType
from app.shared.config import config

logger = logging.getLogger(__name__)


class LiveExecutionHandler(BaseExecutionHandler):
    """
    Обработчик исполнения ордеров для Live-режима.

    Выполняет две ключевые функции:
    1. Отправка рыночных ордеров через REST API соответствующей биржи.
    2. Прослушивание потока сделок (Trades Stream) для получения подтверждений (Fills).

    Attributes:
        events_queue (AsyncQueue): Очередь для отправки событий FillEvent обратно в ядро.
        client (BaseTradeClient): Клиент биржи для отправки ордеров.
        exchange (str): Имя биржи.
        figi_cache (Dict[str, str]): Кэш соответствия Ticker -> FIGI (для Tinkoff).
    """

    def __init__(self, events_queue: AsyncQueue, exchange: str, trade_mode: str = "SANDBOX",
                 loop: asyncio.AbstractEventLoop = None):
        """
        Инициализирует обработчик.

        Args:
            events_queue (AsyncQueue): Очередь событий.
            exchange (str): Биржа ('tinkoff', 'bybit').
            trade_mode (str): Режим торгов ('SANDBOX', 'REAL').
            loop (AbstractEventLoop): Ссылка на event loop (нужен для callback'ов Bybit).
        """
        super().__init__(events_queue)
        self.exchange = exchange
        self.loop = loop
        self.trade_mode = trade_mode
        self.account_id: Optional[str] = None
        self.figi_cache: Dict[str, str] = {}

        # Инициализация клиента биржи
        if exchange == ExchangeType.TINKOFF:
            self.client = TinkoffHandler(trade_mode=trade_mode)
            self.stream_token = config.TINKOFF_TOKEN_SANDBOX if trade_mode == "SANDBOX" else config.TINKOFF_TOKEN_FULL_ACCESS
        elif exchange == ExchangeType.BYBIT:
            self.client = BybitHandler(trade_mode=trade_mode)
        else:
            raise ValueError(f"Неподдерживаемая биржа: {exchange}")

        # Запуск фоновой задачи прослушивания сделок
        self.fill_listener_task = asyncio.create_task(self._listen_for_fills())

    async def _resolve_figi(self, instrument: str) -> str:
        """
        Находит FIGI по тикеру с кэшированием (для Tinkoff).
        Это предотвращает лишние API-запросы при каждом ордере.
        """
        if instrument in self.figi_cache:
            return self.figi_cache[instrument]

        class_code = config.EXCHANGE_SPECIFIC_CONFIG['tinkoff']['DEFAULT_CLASS_CODE']
        logger.info(f"LiveExecutionHandler (Tinkoff): Поиск FIGI для {instrument}...")

        async with AsyncClient(token=config.TINKOFF_TOKEN_READONLY) as client:
            response = await client.instruments.find_instrument(query=instrument)
            instrument_info = next((instr for instr in response.instruments if instr.class_code == class_code), None)

            if not instrument_info:
                raise ValueError(f"Инструмент '{instrument}' (class={class_code}) не найден.")

            self.figi_cache[instrument] = instrument_info.figi
            logger.info(f"Найден FIGI: {instrument_info.figi}")
            return instrument_info.figi

    async def execute_order(self, event: OrderEvent, last_candle: pd.Series = None):
        """
        Асинхронно отправляет рыночный ордер на биржу.

        Args:
            event (OrderEvent): Событие ордера с параметрами (тикер, объем, направление).
            last_candle (pd.Series): Не используется в Live (цена определяется рынком),
                                     но требуется интерфейсом.
        """
        try:
            instrument_id = event.instrument

            # Специфика Tinkoff: нужен FIGI вместо тикера
            if self.exchange == ExchangeType.TINKOFF:
                instrument_id = await self._resolve_figi(event.instrument)

            logger.info(f"LiveExecutionHandler: Отправка ордера: {event} (ID: {instrument_id})")

            # Выполняем синхронный вызов клиента в отдельном потоке,
            # чтобы не блокировать asyncio loop
            await asyncio.to_thread(
                self.client.place_market_order,
                instrument_id=instrument_id,
                quantity=event.quantity,
                direction=event.direction.value
            )
        except Exception as e:
            logger.error(f"LiveExecutionHandler: Критическая ошибка при исполнении ордера: {e}", exc_info=True)

    async def _get_tinkoff_sandbox_account_id(self, client: AsyncClient) -> str:
        """Получает ID первого счета в песочнице Tinkoff."""
        accounts_response = await client.sandbox.get_sandbox_accounts()
        if not accounts_response.accounts:
            raise ConnectionError("Не найдено счетов в песочнице Tinkoff.")
        return accounts_response.accounts[0].id

    async def _listen_for_fills(self):
        """
        Основной цикл прослушивания потока сделок.
        Определяет биржу и запускает соответствующий стрим (gRPC или WebSocket).
        """
        logger.info(f"LiveExecutionHandler ({self.exchange}): Запуск стрима исполнений...")

        while True:
            try:
                if self.exchange == ExchangeType.TINKOFF:
                    await self._listen_tinkoff()
                elif self.exchange == ExchangeType.BYBIT:
                    await self._listen_bybit()

            except Exception as e:
                delay = config.LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']
                logger.error(f"LiveExecutionHandler: Ошибка потока исполнений: {e}. Реконнект через {delay} сек...")
                await asyncio.sleep(delay)

    async def _listen_tinkoff(self):
        """Логика прослушивания стрима Tinkoff."""
        async with AsyncClient(token=self.stream_token) as client:
            if self.trade_mode == "SANDBOX":
                self.account_id = await self._get_tinkoff_sandbox_account_id(client)
            # Для REAL аккаунт должен быть уже известен или найден аналогично

            logger.info(f"Tinkoff Fill Stream: Подписка на счет {self.account_id}")

            async for trade in client.orders_stream.trades_stream(accounts=[self.account_id]):
                if trade.execution_report_status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                    logger.info(f"LiveExecutionHandler (Tinkoff): Исполнение: {trade}")

                    # Обратное преобразование FIGI -> Ticker
                    ticker = next((t for t, figi in self.figi_cache.items() if figi == trade.figi), None)
                    if not ticker:
                        # Если тикера нет в кэше, значит мы не торговали им в этой сессии.
                        # Можно сделать обратный запрос к API, но пока пропускаем.
                        continue

                    price = trade.price.units + trade.price.nano / 1e9
                    commission = (trade.commission.units + trade.commission.nano / 1e9) if trade.commission else 0.0
                    direction = TradeDirection.BUY if trade.direction == OrderDirection.ORDER_DIRECTION_BUY else TradeDirection.SELL

                    fill_event = FillEvent(
                        timestamp=trade.time.replace(tzinfo=timezone.utc),
                        instrument=ticker,
                        quantity=trade.quantity,
                        direction=direction,
                        price=price,
                        commission=commission,
                        trigger_reason=TriggerReason.SIGNAL
                    )
                    await self.events_queue.put(fill_event)

    async def _listen_bybit(self):
        """Логика прослушивания WebSocket Bybit."""
        from pybit.unified_trading import WebSocket

        # Создаем Future, который будет ждать разрыва соединения,
        # чтобы асинхронная функция не завершилась сразу
        connection_lost_future = asyncio.get_running_loop().create_future()

        ws = WebSocket(
            testnet=(self.trade_mode == "SANDBOX"),
            channel_type="private",
            api_key=config.BYBIT_TESTNET_API_KEY if self.trade_mode == "SANDBOX" else None,
            api_secret=config.BYBIT_TESTNET_API_SECRET if self.trade_mode == "SANDBOX" else None,
        )

        def handle_execution(message):
            try:
                for trade in message.get("data", []):
                    # execType 'Trade' означает исполнение сделки
                    if trade.get("execType") == "Trade":
                        logger.info(f"LiveExecutionHandler (Bybit): Исполнение: {trade}")

                        side_str = trade['side'].upper()
                        direction = TradeDirection.BUY if side_str == "BUY" else TradeDirection.SELL

                        fill_event = FillEvent(
                            timestamp=datetime.fromtimestamp(int(trade['execTime']) / 1000, tz=timezone.utc),
                            instrument=trade['symbol'],
                            quantity=float(trade['execQty']),
                            direction=direction,
                            price=float(trade['execPrice']),
                            commission=float(trade.get('execFee', 0.0)),
                            trigger_reason=TriggerReason.SIGNAL
                        )
                        # Потокобезопасная отправка в очередь
                        asyncio.run_coroutine_threadsafe(self.events_queue.put(fill_event), self.loop)
            except Exception as e:
                logger.error(f"LiveExecutionHandler (Bybit Callback Error): {e}")

        # Подписка на приватный канал execution
        ws.execution_stream(callback=handle_execution)

        # Мониторинг соединения
        while ws.is_connected():
            await asyncio.sleep(10)

        logger.warning("Bybit: WebSocket поток исполнений отключился.")

    def stop(self):
        """Останавливает фоновую задачу прослушивания."""
        if self.fill_listener_task:
            self.fill_listener_task.cancel()
            logger.info("LiveExecutionHandler stopped.")