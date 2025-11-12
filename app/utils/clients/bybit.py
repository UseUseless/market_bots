import pandas as pd
import logging
import time
from datetime import datetime, timedelta
from typing import List
from tqdm import tqdm

from pybit.unified_trading import HTTP

from app.utils.clients.abc import BaseDataClient, BaseTradeClient, TradeModeType
from config import (EXCHANGE_INTERVAL_MAPS,
                    BYBIT_TESTNET_API_KEY,
                    BYBIT_TESTNET_API_SECRET
                    )

logger = logging.getLogger(__name__)

class BybitClient(BaseDataClient, BaseTradeClient):
    def __init__(self, trade_mode: TradeModeType = "SANDBOX"):
        use_testnet = (trade_mode == "SANDBOX")
        # TODO: Добавить ключи для реальной торговли в .env и search_space.py
        api_key = BYBIT_TESTNET_API_KEY if use_testnet else 'Нет_ничего. Тут будет реальные ключи'
        api_secret = BYBIT_TESTNET_API_SECRET if use_testnet else 'Нет_ничего. Тут будет реальные ключи'

        if not api_key or "Your" in api_key or not api_secret or "Your" in api_secret:
            raise ConnectionError(f"API ключи для Bybit ({trade_mode}) не заданы в .env.")

        self.client = HTTP(
            testnet=use_testnet,
            api_key=api_key,
            api_secret=api_secret,
            timeout=10
        )

        logging.info(f"Торговый клиент Bybit инициализирован в режиме '{trade_mode}'.")

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        instrument_upper = instrument.upper()
        category = kwargs.get("category", "linear")
        logging.info(f"Bybit Client: используется категория '{category}'")
        api_interval = EXCHANGE_INTERVAL_MAPS["bybit"].get(interval)
        if not api_interval:
            logging.error(f"Неподдерживаемый интервал для Bybit: {interval}.")
            return pd.DataFrame()

        limit = 1000  # Максимальное количество свечей за один запрос
        all_candles = []
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        print(
            f"Запрос данных Bybit для {instrument} ({category}) с {(datetime.now() - timedelta(days=days)).date()}...")

        with tqdm(total=days, desc="Прогресс загрузки", unit="дн.") as pbar:
            current_end_ts = end_ts
            while start_ts < current_end_ts:
                try:
                    resp = self.client.get_kline(category=category, symbol=instrument_upper, interval=api_interval,
                                                 limit=limit, end=current_end_ts)
                    if resp['retCode'] != 0:
                        logging.error(f"Ошибка API Bybit для {instrument}: {resp['retMsg']}")
                        break

                    candles = resp['result']['list']
                    if not candles:
                        logging.info(f"Для {instrument} больше нет доступных исторических данных. Завершение загрузки.")
                        break

                    all_candles.extend(candles)
                    current_end_ts = int(candles[-1][0])

                    days_loaded = (datetime.fromtimestamp(end_ts / 1000) - datetime.fromtimestamp(
                        current_end_ts / 1000)).days
                    if days_loaded > pbar.n: pbar.update(days_loaded - pbar.n)

                    if len(candles) < limit:
                        logging.info(f"Получено меньше {limit} свечей, достигнут конец истории для {instrument}.")
                        break

                    time.sleep(0.3)
                except Exception as e:
                    logging.error(f"Непредвиденная ошибка при запросе к Bybit для {instrument}: {e}")
                    break

            if pbar.n < pbar.total:
                pbar.update(pbar.total - pbar.n)

        if not all_candles: return pd.DataFrame()
        df = pd.DataFrame(all_candles, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        df['time'] = pd.to_datetime(df['time'].astype(float), unit='ms')
        df = df[["time", "open", "high", "low", "close", "volume"]]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        return df.sort_values('time').reset_index(drop=True)

    def get_instrument_info(self, instrument: str, **kwargs) -> dict:
        category = kwargs.get("category", "linear")
        instrument_upper = instrument.upper()
        logging.info(f"Bybit Client: Запрос информации об инструменте {instrument} (категория: {category})...")
        try:
            response = self.client.get_instruments_info(category=category, symbol=instrument_upper)
            if response.get("retCode") == 0 and response["result"]["list"]:
                instr_info = response["result"]["list"][0]
                lot_size_filter = instr_info.get("lotSizeFilter", {})
                return {"min_order_qty": float(lot_size_filter.get("minOrderQty", 0)),
                        "qty_step": float(lot_size_filter.get("qtyStep", 0))}
            else:
                logging.error(f"Bybit Client: Ошибка API при получении информации: {response.get('retMsg')}")
                return {}
        except Exception as e:
            logging.error(f"Bybit Client: Не удалось получить информацию об инструменте {instrument}: {e}")
            return {}

    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        logging.info(f"Bybit Client: Запрос топ-{count} ликвидных USDT-фьючерсов по обороту...")
        try:
            tickers_response = self.client.get_tickers(category="linear")
            if tickers_response.get("retCode") != 0:
                logging.error(f"Ошибка API Bybit при получении тикеров: {tickers_response.get('retMsg')}")
                return []
            instruments = tickers_response["result"]["list"]
            for instr in instruments:
                instr['turnover24h'] = float(instr.get('turnover24h', 0))
            sorted_instruments = sorted(instruments, key=lambda x: x['turnover24h'], reverse=True)
            top_tickers = [instr['symbol'] for instr in sorted_instruments[:count]]
            logging.info(f"Получено {len(top_tickers)} самых ликвидных тикеров Bybit.")
            return top_tickers
        except Exception as e:
            logging.error(f"Ошибка при получении списка ликвидных инструментов Bybit: {e}", exc_info=True)
            return []

    def place_market_order(self, instrument_id: str, quantity: float, direction: str, **kwargs):
        category = kwargs.get("category", "linear")
        logging.info(f"Отправка ордера на Bybit: {direction} {quantity} {instrument_id} (category: {category})")

        try:
            response = self.client.place_order(
                category=category,
                symbol=instrument_id,
                side=direction.capitalize(),
                orderType="Market",
                qty=str(quantity)  # pybit ожидает qty как строку
            )
            logging.info(f"Ответ от Bybit: {response}")

            if response.get("retCode") == 0:
                logging.info(f"Заявка {direction} {quantity} {instrument_id} успешно размещена.")
                return response['result']
            else:
                logging.error(f"Ошибка размещения заявки Bybit: {response.get('retMsg')}")
                return None
        except Exception as e:
            logging.error(f"Критическая ошибка при размещении ордера Bybit: {e}")
            return None
