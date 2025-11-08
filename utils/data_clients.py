import pandas as pd
from datetime import timedelta, datetime
import logging
from typing import List, Dict, Any
from tqdm import tqdm
from abc import ABC, abstractmethod
import time

from tinkoff.invest import Client, RequestError, CandleInterval, InstrumentStatus, SecurityTradingStatus
from tinkoff.invest.utils import now, quotation_to_decimal
from config import TOKEN_READONLY, EXCHANGE_SPECIFIC_CONFIG

from pybit.unified_trading import HTTP

from config import EXCHANGE_INTERVAL_MAPS

logger = logging.getLogger('backtester')

class BaseDataClient(ABC):
    @abstractmethod
    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def get_instrument_info(self, instrument: str, category: str = None) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        raise NotImplementedError


class TinkoffClient(BaseDataClient):
    def __init__(self):
        self.read_token = TOKEN_READONLY
        if not self.read_token or "Your" in self.read_token:
            raise ConnectionError("Токен только для чтения (TOKEN_READONLY) не задан в .env.")
        self._check_token()
        logging.info("Клиент Tinkoff готов к работе.")

    def _check_token(self) -> bool:
        try:
            with Client(self.read_token) as client:
                client.users.get_accounts()
            logging.info("Токен Tinkoff 'только для чтения' успешно прошел проверку.")
            return True
        except RequestError as e:
            logging.critical(f"Ошибка проверки токена Tinkoff: {e}")
            raise ConnectionAbortedError(f"Невалидный токен Tinkoff: {e.details}")

    def _resolve_figi(self, instrument: str) -> str:
        """
        ИЗМЕНЕНО: Финальная версия. Логика поиска FIGI с приоритетом на class_code.
        1. Ищет точное совпадение по тикеру И class_code.
        2. Если не найдено, ищет точное совпадение только по тикеру.
        3. Если не найдено, использует лучший результат нечеткого поиска.
        """
        if instrument.startswith("BBG"):
            return instrument

        logger.info(f"Поиск FIGI для тикера '{instrument}'...")

        with Client(self.read_token) as c:
            try:
                found = c.instruments.find_instrument(query=instrument)
                if not found.instruments:
                    raise ValueError(f"Инструмент '{instrument}' не найден через API.")

                instrument_upper = instrument.upper()
                class_code = EXCHANGE_SPECIFIC_CONFIG['tinkoff']['DEFAULT_CLASS_CODE']

                # --- НОВАЯ УСИЛЕННАЯ ЛОГИКА ---

                # 1. СТРОГИЙ ПОИСК: Точное совпадение тикера И class_code
                strict_match = next((
                    instr for instr in found.instruments
                    if instr.ticker == instrument_upper and instr.class_code == class_code
                ), None)

                if strict_match:
                    logger.info(
                        f"Найдено строгое совпадение: тикер='{strict_match.ticker}', class_code='{class_code}' ({strict_match.name})")
                    target_instrument = strict_match
                else:
                    # 2. МЯГКИЙ ПОИСК: Точное совпадение только по тикеру (если строгий не сработал)
                    logger.warning(
                        f"Строгое совпадение для '{instrument_upper}' в class_code='{class_code}' не найдено. Поиск только по тикеру...")
                    exact_match = next((instr for instr in found.instruments if instr.ticker == instrument_upper), None)

                    if exact_match:
                        logger.info(f"Найдено точное совпадение по тикеру: '{exact_match.ticker}' ({exact_match.name})")
                        target_instrument = exact_match
                    else:
                        # 3. НЕЧЕТКИЙ ПОИСК: Если ничего не найдено, берем лучший результат от API
                        logger.warning(
                            f"Точное совпадение для тикера '{instrument_upper}' не найдено. Используется лучший результат нечеткого поиска.")
                        target_instrument = found.instruments[0]

                figi = target_instrument.figi
                logger.info(
                    f"Выбран инструмент: '{target_instrument.name}' с FIGI: {figi} (class_code: {target_instrument.class_code})")
                return figi

            except RequestError as e:
                logger.error(f"Ошибка API при поиске инструмента '{instrument}': {e}")
                raise

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        try:
            figi = self._resolve_figi(instrument)
        except (ValueError, RequestError) as e:
            logging.error(f"Не удалось получить FIGI для '{instrument}'. {e}")
            return pd.DataFrame()

        interval_name = EXCHANGE_INTERVAL_MAPS["tinkoff"].get(interval)
        if not interval_name:
            logging.error(f"Неподдерживаемый интервал для Tinkoff: {interval}")
            return pd.DataFrame()
        api_interval = getattr(CandleInterval, interval_name)

        all_candles = []
        start_date = now() - timedelta(days=days)
        print(f"Запрос данных Tinkoff для {instrument} ({figi}) с {start_date.date()}...")
        try:
            with Client(self.read_token) as c, tqdm(total=days, desc="Прогресс загрузки", unit="дн.") as pbar:
                for candle in c.get_all_candles(figi=figi, from_=start_date, interval=api_interval):
                    current_progress_days = (candle.time.date() - start_date.date()).days
                    if current_progress_days > pbar.n: pbar.update(current_progress_days - pbar.n)
                    all_candles.append({"time": candle.time, "open": self._cast_money(candle.open),
                                        "high": self._cast_money(candle.high), "low": self._cast_money(candle.low),
                                        "close": self._cast_money(candle.close), "volume": candle.volume})
                if pbar.n < days: pbar.update(days - pbar.n)
        except RequestError as e:
            logging.error(f"Ошибка API при получении данных для {figi}: {e.details}")
            return pd.DataFrame()
        df = pd.DataFrame(all_candles)
        if not df.empty: df['time'] = pd.to_datetime(df['time'])
        return df

    def get_instrument_info(self, instrument: str, category: str = None) -> dict:
        logging.info(f"Tinkoff Client: Запрос информации об инструменте {instrument}...")
        try:
            figi = self._resolve_figi(instrument)
            with Client(self.read_token) as client:
                instr_info = client.instruments.get_instrument_by(id_type=1, id=figi).instrument
                return {"lot_size": instr_info.lot,
                        "min_price_increment": float(quotation_to_decimal(instr_info.min_price_increment)),
                        "qty_step": float(instr_info.lot)}
        except Exception as e:
            logging.error(f"Tinkoff Client: Не удалось получить информацию об инструменте {instrument}: {e}")
            return {}

    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        """
        ИЗМЕНЕНО: Запрашивает топ-N ликвидных акций MOEX на основе СУММАРНОГО оборота
        за последний месяц (~31 день).
        """
        logger.info(f"Tinkoff Client: Запрос топ-{count} ликвидных акций MOEX по месячному обороту...")
        try:
            with Client(self.read_token) as client:
                # Шаг 1: Получаем список всех торгуемых рублевых акций для неквалифицированных инвесторов
                all_shares = client.instruments.shares(
                    instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE).instruments

                tqbr_shares = [
                    s for s in all_shares
                    if s.class_code == 'TQBR'
                       and not s.for_qual_investor_flag
                       and s.currency == 'rub'
                       and s.trading_status in [
                           SecurityTradingStatus.SECURITY_TRADING_STATUS_NORMAL_TRADING,
                           SecurityTradingStatus.SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING
                       ]
                ]

                share_data = []

                # Шаг 2: Для каждой акции рассчитываем суммарный оборот за месяц
                for share_info in tqdm(tqbr_shares, desc="Получение месячных оборотов по акциям"):
                    try:
                        # ИЗМЕНЕНИЕ: Запрашиваем дневные свечи за последний 31 день
                        candles_response = client.market_data.get_candles(
                            figi=share_info.figi,
                            from_=now() - timedelta(days=31),
                            to=now(),
                            interval=CandleInterval.CANDLE_INTERVAL_DAY
                        )

                        if candles_response.candles:
                            # ИЗМЕНЕНИЕ: Суммируем обороты по каждой свече
                            total_turnover_rub = 0
                            for candle in candles_response.candles:
                                close_price = float(quotation_to_decimal(candle.close))
                                volume_in_lots = candle.volume
                                lot_size = share_info.lot
                                daily_turnover = close_price * volume_in_lots * lot_size
                                total_turnover_rub += daily_turnover

                            share_data.append({"ticker": share_info.ticker, "turnover_rub": total_turnover_rub})

                        # Уважительная пауза, чтобы не получить бан от API
                        time.sleep(0.1)

                    except RequestError as e:
                        # Если по инструменту нет данных (например, он новый), просто пропускаем его
                        if e.code == 'NOT_FOUND':
                            logger.debug(
                                f"Нет исторических данных для {share_info.ticker}, возможно, новый инструмент. Пропускаем.")
                        else:
                            logger.warning(f"Не удалось получить данные для {share_info.ticker}: {e.details}")
                        continue

                # Шаг 3: Сортируем и возвращаем топ-N тикеров
                sorted_shares = sorted(share_data, key=lambda x: x['turnover_rub'], reverse=True)
                top_tickers = [s['ticker'] for s in sorted_shares[:count]]

                logger.info(f"Получено {len(top_tickers)} самых ликвидных тикеров по месячному обороту.")
                return top_tickers

        except Exception as e:
            logger.error(f"Ошибка API при получении списка ликвидных инструментов Tinkoff: {e}", exc_info=True)
            return []

    @staticmethod
    def _cast_money(money_value) -> float:
        return money_value.units + money_value.nano / 1e9


class BybitClient(BaseDataClient):
    def __init__(self):
        self.client = HTTP(testnet=False, timeout=10)
        logging.info("Клиент Bybit инициализирован с таймаутом 10с.")

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
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
                    resp = self.client.get_kline(category=category, symbol=instrument, interval=api_interval,
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

    def get_instrument_info(self, instrument: str, category: str = "linear") -> dict:
        logging.info(f"Bybit Client: Запрос информации об инструменте {instrument} (категория: {category})...")
        try:
            response = self.client.get_instruments_info(category=category, symbol=instrument)
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