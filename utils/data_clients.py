import pandas as pd
from datetime import timedelta, datetime
import logging
from typing import Literal
from tqdm import tqdm
from abc import ABC, abstractmethod
import time

# --- Библиотеки для Tinkoff ---
from grpc import StatusCode
from tinkoff.invest import Client, RequestError, CandleInterval
from tinkoff.invest.utils import now
from config import TOKEN_READONLY

# --- Библиотеки для Bybit ---
from pybit.unified_trading import HTTP


# --- Абстрактный базовый класс для всех клиентов ---

class BaseDataClient(ABC):
    """Абстрактный 'контракт', которому должен следовать каждый клиент биржи."""

    @abstractmethod
    def get_historical_data(self, instrument: str, interval: str, days: int) -> pd.DataFrame:
        raise NotImplementedError


# --- Клиент для Tinkoff ---

class TinkoffClient(BaseDataClient):
    """Клиент для взаимодействия с Tinkoff Invest API."""

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

    # -> ИЗМЕНЕНИЕ: Новый метод для определения FIGI по тикеру
    def _resolve_figi(self, instrument: str) -> str:
        """Определяет FIGI по тикеру или возвращает instrument, если это уже FIGI."""
        if instrument.startswith("BBG"):
            return instrument  # Это уже FIGI

        logging.info(f"Поиск FIGI для тикера '{instrument}'...")
        with Client(self.read_token) as c:
            try:
                # Этот метод ищет по всем классам инструментов.
                found = c.instruments.find_instrument(query=instrument)

                if not found.instruments:
                    raise ValueError(f"Инструмент '{instrument}' не найден.")

                # Мы ищем первый инструмент с 'class_code' = 'TQBR',
                # чтобы отсечь фьючерсы или акции с других бирж.
                target_instrument = None
                for instr in found.instruments:
                    if instr.class_code == 'TQBR':
                        target_instrument = instr
                        break

                if not target_instrument:
                    # Если не нашли в TQBR, берем самый первый результат как запасной вариант
                    target_instrument = found.instruments[0]
                    logging.warning(f"Инструмент для '{instrument}' не найден в class_code 'TQBR'. "
                                    f"Используется первый доступный: {target_instrument.name} ({target_instrument.class_code})")

                figi = target_instrument.figi
                logging.info(f"Найден FIGI: {figi} для инструмента '{target_instrument.name}'")
                return figi
            except RequestError as e:
                logging.error(f"Ошибка API при поиске инструмента '{instrument}': {e}")
                raise

    # -> ИЗМЕНЕНИЕ: Сигнатура метода приведена к базовому классу
    def get_historical_data(self, instrument: str, interval: str, days: int) -> pd.DataFrame:
        """Получает исторические свечные данные."""
        try:
            figi = self._resolve_figi(instrument)
        except (ValueError, RequestError) as e:
            logging.error(f"Не удалось получить FIGI для '{instrument}'. {e}")
            return pd.DataFrame()

        interval_map = {
            "1min": CandleInterval.CANDLE_INTERVAL_1_MIN, "5min": CandleInterval.CANDLE_INTERVAL_5_MIN,
            "15min": CandleInterval.CANDLE_INTERVAL_15_MIN, "1hour": CandleInterval.CANDLE_INTERVAL_HOUR,
            "1day": CandleInterval.CANDLE_INTERVAL_DAY,
        }
        api_interval = interval_map.get(interval)
        if not api_interval:
            logging.error(f"Неподдерживаемый интервал для Tinkoff: {interval}")
            return pd.DataFrame()

        all_candles = []
        start_date = now() - timedelta(days=days)
        print(f"Запрос данных Tinkoff для {instrument} ({figi}) с {start_date.date()}...")

        try:
            with Client(self.read_token) as c, tqdm(total=days, desc="Прогресс загрузки", unit="дн.") as pbar:
                for candle in c.get_all_candles(figi=figi, from_=start_date, interval=api_interval):
                    current_progress_days = (candle.time.date() - start_date.date()).days
                    if current_progress_days > pbar.n:
                        pbar.update(current_progress_days - pbar.n)
                    all_candles.append({
                        "time": candle.time, "open": self._cast_money(candle.open),
                        "high": self._cast_money(candle.high), "low": self._cast_money(candle.low),
                        "close": self._cast_money(candle.close), "volume": candle.volume,
                    })
                if pbar.n < days: pbar.update(days - pbar.n)
        except RequestError as e:
            logging.error(f"Ошибка API при получении данных для {figi}: {e.details}")
            return pd.DataFrame()

        df = pd.DataFrame(all_candles)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'])
        return df

    @staticmethod
    def _cast_money(money_value) -> float:
        return money_value.units + money_value.nano / 1e9


# --- Клиент для Bybit ---

class BybitClient(BaseDataClient):
    """Клиент для взаимодействия с Bybit API."""

    def __init__(self):
        # Bybit не требует API ключей для публичных данных
        self.client = HTTP(testnet=False)
        logging.info("Клиент Bybit инициализирован.")

    # -> ИЗМЕНЕНИЕ: Реализация метода базового класса
    def get_historical_data(self, instrument: str, interval: str, days: int) -> pd.DataFrame:
        """Получает исторические свечные данные с Bybit."""
        # Словарь для преобразования нашего формата в формат Bybit
        interval_map = {"1min": "1", "5min": "5", "15min": "15", "1hour": "60", "1day": "D"}
        api_interval = interval_map.get(interval)
        if not api_interval:
            logging.error(f"Неподдерживаемый интервал для Bybit: {interval}")
            return pd.DataFrame()

        all_candles = []
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

        print(f"Запрос данных Bybit для {instrument} с {(datetime.now() - timedelta(days=days)).date()}...")

        with tqdm(total=days, desc="Прогресс загрузки", unit="дн.") as pbar:
            while start_ts < end_ts:
                try:
                    # Bybit отдает данные от новых к старым, поэтому запрашиваем с конца
                    resp = self.client.get_kline(
                        category="spot",  # или linear для фьючерсов
                        symbol=instrument,
                        interval=api_interval,
                        limit=1000,  # Максимум за один запрос
                        end=end_ts
                    )
                    if resp['retCode'] != 0:
                        logging.error(f"Ошибка API Bybit: {resp['retMsg']}")
                        break

                    candles = resp['result']['list']
                    if not candles:
                        break  # Данных больше нет

                    all_candles.extend(candles)
                    # Обновляем конечную точку для следующего запроса
                    end_ts = int(candles[-1][0])

                    # Обновляем прогресс-бар
                    loaded_start_date = datetime.fromtimestamp(end_ts / 1000)
                    total_start_date = datetime.fromtimestamp(start_ts / 1000)
                    days_loaded = (datetime.now() - loaded_start_date).days
                    if days_loaded > pbar.n:
                        pbar.update(days_loaded - pbar.n)

                    time.sleep(0.1)  # Уважительная задержка, чтобы не забанили
                except Exception as e:
                    logging.error(f"Непредвиденная ошибка при запросе к Bybit: {e}")
                    break
            if pbar.n < days: pbar.update(days - pbar.n)

        if not all_candles:
            return pd.DataFrame()

        # Стандартизируем DataFrame к нашему формату
        df = pd.DataFrame(all_candles, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
        df['time'] = pd.to_datetime(df['time'], unit='ms')
        df = df[["time", "open", "high", "low", "close", "volume"]]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])

        return df.sort_values('time').reset_index(drop=True)