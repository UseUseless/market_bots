"""
Реализация потока данных для Bybit (WebSocket).

Этот модуль отвечает за подключение к WebSocket API биржи Bybit,
подписку на свечные данные (kline) и их безопасную передачу
в асинхронное ядро приложения.
"""

import asyncio
import logging
from asyncio import Queue as AsyncQueue
from datetime import datetime, timezone

import pandas as pd
from pybit.unified_trading import WebSocket

from app.infrastructure.feeds.live.streams.base import BaseStreamDataHandler
from app.shared.events import MarketEvent
from app.shared.config import config

LIVE_TRADING_CONFIG = config.LIVE_TRADING_CONFIG
logger = logging.getLogger(__name__)


class BybitStreamDataHandler(BaseStreamDataHandler):
    """
    Обработчик стрима данных от Bybit.

    Особенности реализации:
    Библиотека `pybit` запускает WebSocket в отдельном потоке и использует
    синхронные callback-функции. Для передачи данных в основной асинхронный
    цикл приложения (Event Loop) используется `asyncio.run_coroutine_threadsafe`.

    Attributes:
        loop (asyncio.AbstractEventLoop): Ссылка на главный цикл событий.
        channel_type (str): Тип рынка ('linear', 'spot', 'inverse').
        testnet (bool): Флаг использования тестовой сети.
        ws (WebSocket): Экземпляр клиента pybit.
    """

    def __init__(self, events_queue: AsyncQueue, instrument: str, interval_str: str,
                 loop: asyncio.AbstractEventLoop, channel_type: str, testnet: bool):
        """
        Инициализирует обработчик Bybit.

        Args:
            events_queue (AsyncQueue): Очередь для отправки событий.
            instrument (str): Тикер инструмента (например, BTCUSDT).
            interval_str (str): Интервал (например, 1min).
            loop (AbstractEventLoop): Главный Event Loop приложения (нужен для threadsafe вызовов).
            channel_type (str): Категория рынка ('linear' для USDT-перпетуалов).
            testnet (bool): Использовать ли Testnet.
        """
        super().__init__(events_queue, instrument, interval_str)
        self.loop = loop
        self.channel_type = channel_type
        self.testnet = testnet
        self.ws = None

    def _handle_message(self, message: dict):
        """
        Callback-функция, вызываемая библиотекой pybit при получении сообщения.

        ВНИМАНИЕ: Этот метод выполняется в фоновом потоке pybit, а не в главном
        asyncio-цикле. Поэтому нельзя использовать `await` напрямую.

        Args:
            message (dict): JSON-сообщение от WebSocket.
        """
        try:
            # Структура сообщения Bybit V5: {"topic": "...", "data": [...]}
            data_list = message.get("data", [])
            if not data_list:
                return

            for candle_info in data_list:
                # Нас интересуют только закрытые свечи (confirm=True)
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

                    # Thread-safe передача события в главный цикл
                    asyncio.run_coroutine_threadsafe(self.events_queue.put(event), self.loop)

        except (KeyError, ValueError, TypeError) as e:
            logging.error(f"Bybit Stream: Ошибка парсинга сообщения: {e}. Данные: {message}")
        except Exception as e:
            logging.error(f"Bybit Stream: Непредвиденная ошибка в callback: {e}", exc_info=True)

    async def stream_data(self):
        """
        Запускает и поддерживает WebSocket соединение.

        Реализует паттерн "Infinite Retry Loop":
        1. Подключается к WebSocket.
        2. Подписывается на канал kline (свечи).
        3. Мониторит статус соединения.
        4. При разрыве ждет и переподключается.
        """
        # Маппинг интервалов нашего приложения в формат Bybit API
        interval_map = {
            "1min": "1", "3min": "3", "5min": "5", "15min": "15",
            "30min": "30", "1hour": "60", "2hour": "120",
            "4hour": "240", "1day": "D", "1week": "W"
        }
        api_interval = interval_map.get(self.interval_str)

        if not api_interval:
            logging.error(f"Bybit Stream: Неподдерживаемый интервал: {self.interval_str}. "
                          f"Доступны: {list(interval_map.keys())}. Задача остановлена.")
            return

        while True:
            try:
                logging.info(f"Bybit Stream: Подключение к WebSocket (канал: '{self.channel_type}')...")

                # Инициализация клиента pybit (синхронный внутри, но не блокирует наш loop)
                self.ws = WebSocket(
                    testnet=self.testnet,
                    channel_type=self.channel_type
                )

                # Подписка на топик
                self.ws.kline_stream(
                    interval=api_interval,
                    symbol=self.instrument.upper(),
                    callback=self._handle_message
                )
                logging.info(f"Bybit Stream: Подписка активна: kline.{api_interval}.{self.instrument.upper()}")

                # Цикл мониторинга соединения ("Heartbeat")
                while self.ws.is_connected():
                    await asyncio.sleep(5)

                logging.warning("Bybit Stream: WebSocket соединение потеряно.")

            except asyncio.CancelledError:
                logging.info("Bybit Stream: Получена команда на остановку. Закрытие соединения...")
                if self.ws:
                    self.ws.exit()
                raise  # Пробрасываем отмену наверх

            except Exception as e:
                logging.error(f"Bybit Stream: Ошибка соединения: {e}")

            finally:
                # Гарантированное закрытие сокета при рестарте или ошибке
                if self.ws:
                    self.ws.exit()

            # Задержка перед реконнектом
            delay = LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']
            logging.info(f"Переподключение через {delay} секунд...")
            await asyncio.sleep(delay)