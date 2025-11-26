import asyncio
import logging
from asyncio import Queue as AsyncQueue
from datetime import datetime, timezone

import pandas as pd
from pybit.unified_trading import WebSocket

from app.infrastructure.feeds.stream_base import BaseStreamDataHandler
from app.shared.events import MarketEvent
from config import LIVE_TRADING_CONFIG


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
