import asyncio
import logging
from asyncio import Queue as AsyncQueue
from datetime import datetime, timezone
import pandas as pd

from app.core.models.event import OrderEvent, FillEvent
from app.services.execution.abc import BaseExecutionHandler
from app.adapters.exchanges.bybit import BybitHandler
from app.adapters.exchanges.tinkoff import TinkoffHandler
from app.core.interfaces.exchange_client import BaseTradeClient
from app.core.constants import TradeDirection, TriggerReason, ExchangeType

from tinkoff.invest import AsyncClient, OrderExecutionReportStatus, OrderDirection

from config import (TOKEN_SANDBOX, TOKEN_READONLY, BYBIT_TESTNET_API_KEY,
                    BYBIT_TESTNET_API_SECRET, TOKEN_FULL_ACCESS,
                    LIVE_TRADING_CONFIG, EXCHANGE_SPECIFIC_CONFIG)


class LiveExecutionHandler(BaseExecutionHandler):
    """
    Исполняет ордера через API биржи (в режиме "песочницы" или реальном)
    и слушает стрим для получения информации об исполнении.
    """

    def __init__(self, events_queue: AsyncQueue, exchange: str, trade_mode: str = "SANDBOX",
                 loop: asyncio.AbstractEventLoop = None):
        super().__init__(events_queue)
        self.client: BaseTradeClient
        self.exchange = exchange
        self.loop = loop
        self.trade_mode = trade_mode
        self.account_id = None
        self.figi_cache = {}

        if exchange == ExchangeType.TINKOFF:
            self.client = TinkoffHandler(trade_mode=trade_mode)
            self.stream_token = TOKEN_SANDBOX if trade_mode == "SANDBOX" else TOKEN_FULL_ACCESS
        elif exchange == ExchangeType.BYBIT:
            self.client = BybitHandler(trade_mode=trade_mode)
        else:
            raise ValueError(f"Неподдерживаемая биржа: {exchange}")

        self.fill_listener_task = asyncio.create_task(self._listen_for_fills())

    async def _resolve_figi(self, instrument: str) -> str:
        """Находит и кэширует FIGI для тикера."""
        if instrument in self.figi_cache:
            return self.figi_cache[instrument]

        class_code = EXCHANGE_SPECIFIC_CONFIG['tinkoff']['DEFAULT_CLASS_CODE']
        logging.info(f"LiveExecutionHandler (Tinkoff): Поиск FIGI для {instrument}...")
        async with AsyncClient(token=TOKEN_READONLY) as client:
            response = await client.instruments.find_instrument(query=instrument)
            instrument_info = next((instr for instr in response.instruments if instr.class_code == class_code), None)
            if not instrument_info:
                raise ValueError(f"Инструмент '{instrument}' не найден.")

            self.figi_cache[instrument] = instrument_info.figi
            logging.info(f"Найден FIGI: {instrument_info.figi}")
            return instrument_info.figi

    async def execute_order(self, event: OrderEvent, last_candle: pd.Series = None):
        """Асинхронно отправляет рыночный ордер через API."""
        try:
            instrument_id = event.instrument
            if self.exchange == ExchangeType.TINKOFF:
                instrument_id = await self._resolve_figi(event.instrument)

            logging.info(f"LiveExecutionHandler: Отправка ордера: {event} (ID: {instrument_id})")

            # Используем to_thread, так как клиенты (requests/grpc) могут быть синхронными или блокирующими
            await asyncio.to_thread(
                self.client.place_market_order,
                instrument_id=instrument_id,
                quantity=event.quantity,
                # Передаем строку "BUY"/"SELL" в клиент, так как он ожидает строку (или Enum.value)
                direction=event.direction.value
            )
        except Exception as e:
            logging.error(f"LiveExecutionHandler: Критическая ошибка при исполнении ордера: {e}")

    async def _get_tinkoff_sandbox_account_id(self, client: AsyncClient) -> str:
        """Получает ID первого доступного счета в песочнице."""
        accounts_response = await client.sandbox.get_sandbox_accounts()
        if not accounts_response.accounts:
            raise ConnectionError("Не найдено счетов в песочнице Tinkoff.")
        return accounts_response.accounts[0].id

    async def _listen_for_fills(self):
        """
        Фоновая задача для прослушивания исполнений.
        """
        logging.info(f"LiveExecutionHandler ({self.exchange}): Запуск прослушивания исполненных ордеров...")

        while True:
            try:
                if self.exchange == ExchangeType.TINKOFF:
                    async with AsyncClient(token=self.stream_token) as client:
                        if self.trade_mode == "SANDBOX":
                            self.account_id = await self._get_tinkoff_sandbox_account_id(client)
                        # Для REAL ID счета должен быть передан или найден иначе, но для примера ок

                        logging.info(f"Tinkoff Stream: Прослушивание сделок на счете {self.account_id}")

                        async for trade in client.orders_stream.trades_stream(accounts=[self.account_id]):
                            if trade.execution_report_status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
                                logging.info(f"LiveExecutionHandler (Tinkoff): Получено исполнение: {trade}")

                                ticker = next(
                                    (t for t, figi in self.figi_cache.items() if figi == trade.figi), None)

                                if not ticker:
                                    # Если тикер не в кэше, можно попробовать найти или игнорировать
                                    continue

                                price = trade.price.units + trade.price.nano / 1e9
                                commission = (
                                            trade.commission.units + trade.commission.nano / 1e9) if trade.commission else 0.0

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

                elif self.exchange == ExchangeType.BYBIT:
                    from pybit.unified_trading import WebSocket
                    ws = WebSocket(
                        testnet=(self.trade_mode == "SANDBOX"),
                        channel_type="private",
                        api_key=BYBIT_TESTNET_API_KEY,
                        api_secret=BYBIT_TESTNET_API_SECRET,
                    )

                    def handle_execution(message):
                        try:
                            for trade in message.get("data", []):
                                if trade.get("execType") == "Trade":
                                    logging.info(f"LiveExecutionHandler (Bybit): Получено исполнение: {trade}")

                                    side_str = trade['side'].upper()
                                    direction = TradeDirection.BUY if side_str == TradeDirection.BUY else TradeDirection.SELL

                                    fill_event = FillEvent(
                                        timestamp=datetime.fromtimestamp(int(trade['execTime']) / 1000,
                                                                         tz=timezone.utc),
                                        instrument=trade['symbol'],
                                        quantity=float(trade['execQty']),
                                        direction=direction,
                                        price=float(trade['execPrice']),
                                        commission=float(trade.get('execFee', 0.0)),
                                        # ИСПРАВЛЕНИЕ 3
                                        trigger_reason=TriggerReason.SIGNAL
                                    )
                                    asyncio.run_coroutine_threadsafe(self.events_queue.put(fill_event), self.loop)
                        except Exception as e:
                            logging.error(f"LiveExecutionHandler (Bybit): Ошибка обработки: {e}")

                    ws.execution_stream(callback=handle_execution)

                    while ws.is_connected():
                        await asyncio.sleep(60)
                    logging.warning("Bybit: WebSocket поток сделок отключился.")

            except Exception as e:
                logging.error(
                    f"LiveExecutionHandler: Ошибка потока: {e}. Реконнект через {LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']} сек...")
                await asyncio.sleep(LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS'])

    def stop(self):
        if self.fill_listener_task:
            self.fill_listener_task.cancel()
            logging.info("LiveExecutionHandler stopped.")