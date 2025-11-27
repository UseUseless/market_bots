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


class TinkoffStreamDataHandler(BaseStreamDataHandler):
    """Получает live-свечи через gRPC-стрим Tinkoff, используя Stream Manager."""

    async def stream_data(self):
        """
        Основной метод, который в бесконечном цикле пытается подключиться
        к стриму данных Tinkoff.
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
                    response = await client.instruments.find_instrument(query=self.instrument)
                    instrument_info = next((instr for instr in response.instruments if instr.class_code == 'TQBR'),
                                           None)

                    if not instrument_info:
                        logging.error(f"Tinkoff Stream: Инструмент '{self.instrument}' не найден. "
                                      f"Повторная попытка через {LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']} сек.")
                        await asyncio.sleep(LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS'])
                        continue

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

            except asyncio.CancelledError:
                logging.info("Tinkoff Stream: Получена команда на остановку. Корректный выход.")
                raise # Пробрасываем отмену наверх, чтобы loop.py знал, что мы закончили

            except Exception as e:
                # Если на любом из этапов внутри try произошла ошибка, мы попадаем сюда.
                logging.error(f"Tinkoff Stream: Критическая ошибка в потоке данных: {e}. "
                              f"Переподключение через {LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS']} секунд...")
                await asyncio.sleep(LIVE_TRADING_CONFIG['LIVE_RECONNECT_DELAY_SECONDS'])

    @staticmethod
    def _cast_money(quotation) -> float:
        return quotation.units + quotation.nano / 1e9
