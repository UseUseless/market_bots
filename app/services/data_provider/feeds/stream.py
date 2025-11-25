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
from config import TOKEN_READONLY, LIVE_TRADING_CONFIG


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
        """
        Основной метод, который в бесконечном цикле пытается подключиться
        к стриму данных Tinkoff и получать свечи. В случае обрыва связи или
        другой ошибки, он делает паузу и начинает процесс подключения заново.
        """
        from tinkoff.invest import CandleInstrument, SubscriptionInterval

        interval_map = {
            "1min": SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_MINUTE,
            "5min": SubscriptionInterval.SUBSCRIPTION_INTERVAL_FIVE_MINUTES,
        }
        api_interval = interval_map.get(self.interval_str)
        if not api_interval:
            logging.error(f"Tinkoff Stream: Неподдерживаемый интервал: {self.interval_str}. Доступны: 1min, 5min. Задача остановлена.")
            return

        # Внешний "вечный" цикл для обеспечения переподключения
        while True:
            try:
                # Вся логика подключения и получения данных находится внутри try-блока
                async with AsyncClient(token=TOKEN_READONLY) as client:
                    logging.info("Tinkoff Stream: Попытка подключения и поиска FIGI...")

                    # 1. Поиск FIGI
                    # (Логика поиска FIGI остается прежней)
                    response = await client.instruments.find_instrument(query=self.instrument)
                    instrument_info = next((instr for instr in response.instruments if instr.class_code == 'TQBR'),
                                           None)

                    if not instrument_info:
                        logging.error(f"Tinkoff Stream: Инструмент '{self.instrument}' не найден. "
                                      f"Повторная попытка через {LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']} сек.")
                        await asyncio.sleep(LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS'])
                        continue  # Переходим к следующей итерации while True

                    figi = instrument_info.figi
                    logging.info(f"Tinkoff Stream: Найден FIGI: {figi}. Подключение к стриму...")

                    # 2. Создание и настройка менеджера стримов
                    market_data_stream: AsyncMarketDataStreamManager = client.create_market_data_stream()
                    market_data_stream.candles.waiting_close().subscribe(
                        [CandleInstrument(figi=figi, interval=api_interval)]
                    )

                    # 3. Основной цикл получения данных
                    logging.info("Tinkoff Stream: Успешно подключено. Ожидание рыночных данных...")
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
                # Если на любом из этапов внутри try произошла ошибка, мы попадаем сюда.
                logging.error(f"Tinkoff Stream: Критическая ошибка в потоке данных: {e}. "
                              f"Переподключение через {LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']} секунд...")
                await asyncio.sleep(LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS'])

    @staticmethod
    def _cast_money(quotation) -> float:
        return quotation.units + quotation.nano / 1e9


class BybitStreamDataHandler(BaseStreamDataHandler):
    """Получает live-свечи через WebSocket Bybit."""

    def __init__(self, events_queue: AsyncQueue, instrument: str, interval_str: str,
                 loop: asyncio.AbstractEventLoop, channel_type: str, testnet: bool):
        super().__init__(events_queue, instrument, interval_str)
        self.loop = loop
        self.channel_type = channel_type
        self.testnet = testnet
        self.ws = None

    def _handle_message(self, message: dict):
        """
        Callback-функция для обработки сообщений от WebSocket.
        Теперь с надежной обработкой ошибок.
        """
        try:
            # Проверяем, что это данные свечи и она закрылась ('confirm': True)
            data_list = message.get("data", [])
            if not data_list:
                return

            for candle_info in data_list:
                if candle_info.get("confirm") is True:
                    candle_data = pd.Series({
                        "time": datetime.fromtimestamp(int(candle_info['start']) / 1000, tz=timezone.utc),
                        "open": float(candle_info['open']),
                        "high": float(candle_info['high']),
                        "low": float(candle_info['low']),
                        "close": float(candle_info['close']),
                        "volume": float(candle_info['volume']),
                    })

                    event = MarketEvent(
                        timestamp=candle_data['time'],
                        instrument=self.instrument,
                        data=candle_data
                    )
                    # Безопасно кладем событие в асинхронную очередь из синхронного callback
                    asyncio.run_coroutine_threadsafe(self.events_queue.put(event), self.loop)

        except (KeyError, ValueError, TypeError) as e:
            logging.error(f"Bybit Stream: Ошибка парсинга сообщения: {e}. Сообщение: {message}")
        except Exception as e:
            logging.error(f"Bybit Stream: Непредвиденная ошибка в callback: {e}", exc_info=True)

    async def stream_data(self):
        """
        Основной метод, который запускает WebSocket и следит за его состоянием,
        перезапуская при необходимости.
        """
        interval_map = {"1min": "1", "3min": "3", "5min": "5", "15min": "15", "30min": "30", "1hour": "60", "1day": "D"}
        api_interval = interval_map.get(self.interval_str)
        if not api_interval:
            logging.error(f"Bybit Stream: Неподдерживаемый интервал: {self.interval_str}.")
            return

        # Внешний "вечный" цикл для переподключения
        while True:
            try:
                logging.info(f"Bybit Stream: Попытка подключения к WebSocket (канал: '{self.channel_type}')...")
                self.ws = WebSocket(
                    testnet=self.testnet,
                    channel_type=self.channel_type
                )

                # Подписываемся на поток kline, передавая наш защищенный callback
                self.ws.kline_stream(
                    interval=api_interval,
                    symbol=self.instrument.upper(),
                    callback=self._handle_message
                )
                logging.info(f"Bybit Stream: Успешно подписались на kline.{api_interval}.{self.instrument.upper()}.")

                # Внутренний цикл-"наблюдатель" (watchdog)
                while self.ws.is_connected():
                    # Просто спим и периодически проверяем соединение
                    await asyncio.sleep(15)

                    # Если мы вышли из этого цикла, значит, соединение было потеряно
                logging.warning("Bybit Stream: WebSocket соединение потеряно.")

            except Exception as e:
                logging.error(f"Bybit Stream: Критическая ошибка при установке соединения: {e}")

            finally:
                # В любом случае (обрыв или ошибка) останавливаем текущий экземпляр WebSocket, если он есть
                if self.ws:
                    self.ws.exit()

                logging.info(f"Переподключение через {LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']} секунд...")
                await asyncio.sleep(LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS'])
