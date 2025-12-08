"""
Реализация потока данных для Tinkoff Invest API.

Этот модуль отвечает за подключение к gRPC стриму Тинькофф Инвестиций,
подписку на свечи (Candles) и трансляцию их в систему в формате `MarketEvent`.
"""

import asyncio
import logging
from datetime import timezone

import pandas as pd
from tinkoff.invest import AsyncClient
from tinkoff.invest.market_data_stream.async_market_data_stream_manager import AsyncMarketDataStreamManager

from app.infrastructure.feeds.stream_base import BaseStreamDataHandler
from app.shared.events import MarketEvent
from app.shared.config import config

LIVE_TRADING_CONFIG = config.LIVE_TRADING_CONFIG
TOKEN_READONLY = config.TINKOFF_TOKEN_READONLY

logger = logging.getLogger(__name__)


class TinkoffStreamDataHandler(BaseStreamDataHandler):
    """
    Обработчик стрима данных от Tinkoff (gRPC).

    Использует `AsyncMarketDataStreamManager` для управления подписками.
    Обеспечивает автоматическое переподключение при разрывах связи.
    """

    def __init__(self, events_queue, instrument, interval_str, token):
        super().__init__(events_queue, instrument, interval_str)
        self.token = token

    async def stream_data(self):
        """
        Запускает бесконечный цикл получения рыночных данных.

        Алгоритм работы:
        1. Входит в бесконечный цикл `while True` для обеспечения реконнектов.
        2. Создает gRPC клиента.
        3. Ищет FIGI инструмента по тикеру (требование API Tinkoff).
        4. Подписывается на поток свечей (`candles.waiting_close()`).
        5. При получении закрытой свечи конвертирует данные и отправляет `MarketEvent`.

        Обработка ошибок:
        - При `CancelledError` (остановка бота) корректно завершает работу.
        - При любых других ошибках (сети, API) ждет заданное время и пробует снова.
        """
        # Импорт внутри метода, чтобы избежать циклических зависимостей или
        # загрузки тяжелых модулей, если используется другой провайдер.
        from tinkoff.invest import CandleInstrument, SubscriptionInterval

        interval_map = {
            "1min": SubscriptionInterval.SUBSCRIPTION_INTERVAL_ONE_MINUTE,
            "5min": SubscriptionInterval.SUBSCRIPTION_INTERVAL_FIVE_MINUTES,
        }
        api_interval = interval_map.get(self.interval_str)

        if not api_interval:
            logging.error(f"Tinkoff Stream: Неподдерживаемый интервал: {self.interval_str}. "
                          f"Доступны: {list(interval_map.keys())}. Задача остановлена.")
            return

        # Внешний "вечный" цикл для обеспечения переподключения
        while True:
            try:
                # Вся логика подключения и получения данных находится внутри try-блока
                async with AsyncClient(token=self.token) as client:
                    logging.info(f"Tinkoff Stream: Поиск FIGI для {self.instrument}...")

                    # 1. Поиск FIGI (нужен для подписки)
                    # Фильтруем только TQBR (акции Мосбиржи), чтобы избежать путаницы с фьючерсами/фондами
                    response = await client.instruments.find_instrument(query=self.instrument)
                    instrument_info = next((instr for instr in response.instruments if instr.class_code == 'TQBR'),
                                           None)

                    if not instrument_info:
                        delay = LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']
                        logging.error(f"Tinkoff Stream: Инструмент '{self.instrument}' (TQBR) не найден. "
                                      f"Повторная попытка через {delay} сек.")
                        await asyncio.sleep(delay)
                        continue

                    figi = instrument_info.figi
                    logging.info(f"Tinkoff Stream: Найден FIGI: {figi}. Подключение к стриму...")

                    # 2. Создание и настройка менеджера стримов
                    market_data_stream: AsyncMarketDataStreamManager = client.create_market_data_stream()

                    # Подписываемся на свечи. waiting_close() означает, что мы получаем
                    # событие только когда свеча полностью сформировалась.
                    market_data_stream.candles.waiting_close().subscribe(
                        [CandleInstrument(figi=figi, interval=api_interval)]
                    )

                    # 3. Основной цикл получения данных
                    logging.info("Tinkoff Stream: Успешно подключено. Ожидание рыночных данных...")

                    async for marketdata in market_data_stream:
                        if marketdata.candle:
                            candle = marketdata.candle

                            # Преобразование во внутренний формат
                            candle_data = pd.Series({
                                "time": candle.time.replace(tzinfo=timezone.utc),
                                "open": self._cast_money(candle.open),
                                "high": self._cast_money(candle.high),
                                "low": self._cast_money(candle.low),
                                "close": self._cast_money(candle.close),
                                "volume": candle.volume,
                            })

                            event = MarketEvent(
                                timestamp=candle_data['time'],
                                instrument=self.instrument,
                                data=candle_data
                            )
                            # Отправка в очередь для обработки стратегией
                            await self.events_queue.put(event)

            except asyncio.CancelledError:
                logging.info("Tinkoff Stream: Получена команда на остановку. Корректный выход.")
                raise  # Пробрасываем отмену наверх, чтобы loop.py знал, что мы закончили

            except Exception as e:
                delay = LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']
                logging.error(f"Tinkoff Stream: Ошибка в потоке данных: {e}. "
                              f"Переподключение через {delay} секунд...")
                await asyncio.sleep(delay)

    @staticmethod
    def _cast_money(quotation) -> float:
        """
        Конвертирует структуру Quotation/MoneyValue (units + nano) в float.

        Args:
            quotation: Объект цены от API Tinkoff.

        Returns:
            float: Цена в виде дробного числа.
        """
        return quotation.units + quotation.nano / 1e9