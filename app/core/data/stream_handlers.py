import asyncio
import logging
from abc import ABC, abstractmethod
from asyncio import Queue as AsyncQueue
from datetime import timezone, datetime

import pandas as pd
from pybit.unified_trading import WebSocket
from tinkoff.invest import AsyncClient
from tinkoff.invest.market_data_stream.async_market_data_stream_manager import AsyncMarketDataStreamManager

from app.core.models.event import MarketEvent
from config import TOKEN_READONLY


class BaseStreamDataHandler(ABC):
    """Абстрактный 'контракт' для всех поставщиков live-данных."""

    def __init__(self, events_queue: AsyncQueue, instrument: str, interval_str: str):
        self.events_queue = events_queue
        self.instrument = instrument
        self.interval_str = interval_str

    @abstractmethod
    async def stream_data(self):
        """Основная асинхронная задача, которая слушает данные и кладет их в очередь."""
        raise NotImplementedError


class TinkoffStreamDataHandler(BaseStreamDataHandler):
    """Получает live-свечи через gRPC-стрим Tinkoff, используя Stream Manager."""

    async def stream_data(self):
        from tinkoff.invest import CandleInstrument, SubscriptionInterval

        interval_map = {
            "1min": SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_MINUTE,
            "5min": SubscriptionInterval.SUBSCRIPTION_INTERVAL_FIVE_MINUTES,
        }
        api_interval = interval_map.get(self.interval_str)
        if not api_interval:
            logging.error(f"Tinkoff Stream: Неподдерживаемый интервал: {self.interval_str}. Доступны: 1min, 5min.")
            return

        # Используем AsyncClient
        async with AsyncClient(token=TOKEN_READONLY) as client:
            logging.info("Tinkoff Stream: Поиск FIGI для инструмента...")
            try:
                response = await client.instruments.find_instrument(query=self.instrument)
                instrument_info = next((instr for instr in response.instruments if instr.class_code == 'TQBR'), None)
                if not instrument_info:
                    logging.error(f"Tinkoff Stream: Инструмент '{self.instrument}' не найден.")
                    return
                figi = instrument_info.figi
                logging.info(f"Tinkoff Stream: Найден FIGI: {figi}. Подключение к стриму...")
            except Exception as e:
                logging.error(f"Tinkoff Stream: Ошибка при поиске инструмента: {e}")
                return

            # Создаем и настраиваем менеджер стримов
            market_data_stream: AsyncMarketDataStreamManager = client.create_market_data_stream()

            # Подписываемся на получение только закрытых (is_complete=True) свечей
            market_data_stream.candles.waiting_close().subscribe(
                [CandleInstrument(figi=figi, interval=api_interval)]
            )

            # --- Основной цикл стрима ---
            try:
                async for marketdata in market_data_stream:
                    if marketdata.candle:
                        candle = marketdata.candle
                        candle_data = pd.Series({
                            "time": candle.time.replace(tzinfo=timezone.utc),
                            "open": self._cast_money(candle.open), "high": self._cast_money(candle.high),
                            "low": self._cast_money(candle.low), "close": self._cast_money(candle.close),
                            "volume": candle.volume,
                        })

                        event = MarketEvent(
                            timestamp=candle_data['time'],
                            instrument=self.instrument,
                            data=candle_data
                        )
                        await self.events_queue.put(event)
            except Exception as e:
                logging.error(f"Tinkoff Stream: Критическая ошибка в потоке данных: {e}")
            finally:
                # Корректно останавливаем стрим при выходе
                market_data_stream.stop()

    @staticmethod
    def _cast_money(quotation) -> float:
        return quotation.units + quotation.nano / 1e9


class BybitStreamDataHandler(BaseStreamDataHandler):
    """Получает live-свечи через WebSocket Bybit."""

    def __init__(self, events_queue: AsyncQueue, instrument: str, interval_str: str, loop: asyncio.AbstractEventLoop, channel_type: str, testnet: bool):
        super().__init__(events_queue, instrument, interval_str)
        self.loop = loop
        self.channel_type = channel_type
        self.testnet = testnet

    async def stream_data(self):
        interval_map = {"1min": "1", "5min": "5", "15min": "15", "1hour": "60", "1day": "D"}
        api_interval = interval_map.get(self.interval_str)
        if not api_interval:
            logging.error(f"Bybit Stream: Неподдерживаемый интервал: {self.interval_str}.")
            return

        ws = WebSocket(testnet=self.testnet, channel_type=self.channel_type)
        logging.info(f"Bybit Stream: используется канал '{self.channel_type}'")

        # Callback-функция, которая будет обрабатывать сообщения от WebSocket
        def handle_message(message):
            try:
                data = message.get("data", [{}])[0]
                # -> ИЗМЕНЕНИЕ 2: Проверяем, что это сообщение о закрытии свечи
                if data.get("confirm") == True:
                    candle_data = pd.Series({
                        "time": datetime.fromtimestamp(int(data['start']) / 1000, tz=timezone.utc),
                        "open": float(data['open']), "high": float(data['high']),
                        "low": float(data['low']), "close": float(data['close']),
                        "volume": float(data['volume']),
                    })

                    event = MarketEvent(
                        timestamp=candle_data['time'],
                        instrument=self.instrument,
                        data=candle_data
                    )
                    # ВАЖНО: Кладем в очередь из синхронного callback'а
                    asyncio.run_coroutine_threadsafe(self.events_queue.put(event), self.loop)
            except Exception as e:
                logging.error(f"Bybit Stream: Ошибка обработки сообщения: {e}")

        ws.kline_stream(
            interval=api_interval,
            symbol=self.instrument.upper(),
            callback=handle_message
        )
        logging.info(f"Bybit Stream: Подписка на kline.{api_interval}.{self.instrument.upper()}...")

        # Бесконечный цикл, чтобы поддерживать соединение
        while True:
            await asyncio.sleep(3600)  # Просто спим, вся работа идет в callback'е
