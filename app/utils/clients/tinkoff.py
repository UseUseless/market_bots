import logging
import time
from datetime import timedelta
from typing import List, Dict, Any
from tqdm import tqdm
import pandas as pd

from tinkoff.invest import (
    Client, RequestError,
    CandleInterval, InstrumentStatus,
    SecurityTradingStatus,
    OrderDirection, OrderType
)
from tinkoff.invest.utils import now, quotation_to_decimal

from app.utils.clients.abc import TradeModeType, BaseTradeClient, BaseDataClient
from app.core.constants import TradeDirection
from config import (
    TOKEN_READONLY, TOKEN_FULL_ACCESS,
    TOKEN_SANDBOX, ACCOUNT_ID,
    EXCHANGE_SPECIFIC_CONFIG, EXCHANGE_INTERVAL_MAPS
)

logger = logging.getLogger(__name__)


class TinkoffHandler(BaseDataClient, BaseTradeClient):
    """
    Единый клиент для работы с Tinkoff Invest API.
    Реализует интерфейсы для получения данных и для торговли.
    """

    def __init__(self, trade_mode: TradeModeType = "SANDBOX"):
        self.read_token = TOKEN_READONLY
        self.trade_mode = trade_mode.upper()
        self.trade_token: str | None = None

        # Единое хранилище ID счета (и для Real, и для Sandbox)
        self.account_id: str | None = ACCOUNT_ID

        # Проверка токенов
        if not self.read_token or "Your" in self.read_token:
            raise ConnectionError("Токен только для чтения (TOKEN_READONLY) не задан в .env.")

        if self.trade_mode == "REAL":
            self.trade_token = TOKEN_FULL_ACCESS
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен с полным доступом (TOKEN_FULL_ACCESS) не задан в .env.")

            # Инициализация счета для Real
            if not self.account_id:
                logging.info("ID реального счета не указан, будет использован первый доступный.")
                self.account_id = self._get_first_account_id(sandbox=False)

        elif self.trade_mode == "SANDBOX":
            self.trade_token = TOKEN_SANDBOX
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен песочницы (TOKEN_SANDBOX) не задан в .env.")

            # Инициализация счета для Sandbox (всегда берем, т.к. в .env обычно реальный ID)
            # Либо можно добавить TINKOFF_SANDBOX_ACCOUNT_ID в конфиг, но проще найти авто.
            self.account_id = self._get_first_account_id(sandbox=True)
            logging.info(f"Используется счет песочницы: {self.account_id}")

        else:
            raise ValueError(f"Неподдерживаемый торговый режим: {trade_mode}")

        logging.info(
            f"Торговый клиент Tinkoff инициализирован в режиме '{self.trade_mode}'. Account ID: {self.account_id}")
        self._check_token()

    def _check_token(self) -> bool:
        try:
            with Client(self.read_token) as client:
                client.users.get_accounts()
            logging.info("Токен Tinkoff 'только для чтения' успешно прошел проверку.")
            return True
        except RequestError as e:
            logging.critical(f"Ошибка проверки токена Tinkoff: {e}")
            raise ConnectionAbortedError(f"Невалидный токен Tinkoff: {e.details}")

    def _get_first_account_id(self, sandbox: bool = False) -> str:
        """Получает ID первого доступного счета (Real или Sandbox)."""
        try:
            with Client(self.trade_token) as client:
                if sandbox:
                    accounts = client.sandbox.get_sandbox_accounts().accounts
                else:
                    accounts = client.users.get_accounts().accounts

                if not accounts:
                    # Если в песочнице нет счетов, создадим один
                    if sandbox:
                        logging.info("Счета в песочнице не найдены. Открываем новый...")
                        client.sandbox.open_sandbox_account()
                        # Повторный запрос
                        accounts = client.sandbox.get_sandbox_accounts().accounts

                    if not accounts:
                        raise ConnectionError(f"Не найдено счетов в режиме {'SANDBOX' if sandbox else 'REAL'}.")

                return accounts[0].id
        except RequestError as e:
            logging.critical(f"Критическая ошибка получения ID счета: {e}")
            raise

    def _resolve_figi(self, instrument: str) -> str:
        """Поиск FIGI по тикеру."""
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

                # 1. Строгий поиск
                strict_match = next((
                    instr for instr in found.instruments
                    if instr.ticker == instrument_upper and instr.class_code == class_code
                ), None)

                if strict_match:
                    target_instrument = strict_match
                else:
                    # 2. Мягкий поиск
                    logger.warning(f"Строгое совпадение для '{instrument_upper}' не найдено. Ищем по тикеру.")
                    exact_match = next((instr for instr in found.instruments if instr.ticker == instrument_upper), None)
                    target_instrument = exact_match if exact_match else found.instruments[0]

                logger.info(f"Выбран: '{target_instrument.name}' (FIGI: {target_instrument.figi})")
                return target_instrument.figi

            except RequestError as e:
                logger.error(f"Ошибка API при поиске '{instrument}': {e}")
                raise

    @staticmethod
    def _cast_money(money_value) -> float:
        return money_value.units + money_value.nano / 1e9

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        try:
            figi = self._resolve_figi(instrument)
        except (ValueError, RequestError) as e:
            logging.error(f"FIGI error for '{instrument}': {e}")
            return pd.DataFrame()

        interval_name = EXCHANGE_INTERVAL_MAPS["tinkoff"].get(interval)
        if not interval_name:
            logging.error(f"Неподдерживаемый интервал: {interval}")
            return pd.DataFrame()
        api_interval = getattr(CandleInterval, interval_name)

        all_candles = []
        start_date = now() - timedelta(days=days)
        print(f"Запрос данных Tinkoff для {instrument} ({figi}) с {start_date.date()}...")

        try:
            with Client(self.read_token) as c, tqdm(total=days, desc="Загрузка", unit="дн.") as pbar:
                for candle in c.get_all_candles(figi=figi, from_=start_date, interval=api_interval):
                    # Обновление прогресса (приблизительно)
                    current_progress_days = (candle.time.date() - start_date.date()).days
                    if current_progress_days > pbar.n: pbar.update(current_progress_days - pbar.n)

                    all_candles.append({
                        "time": candle.time,
                        "open": self._cast_money(candle.open),
                        "high": self._cast_money(candle.high),
                        "low": self._cast_money(candle.low),
                        "close": self._cast_money(candle.close),
                        "volume": candle.volume
                    })
                if pbar.n < days: pbar.update(days - pbar.n)
        except RequestError as e:
            logging.error(f"Ошибка API при получении данных: {e.details}")
            return pd.DataFrame()

        df = pd.DataFrame(all_candles)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], utc=True)
        return df

    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        try:
            figi = self._resolve_figi(instrument)
            with Client(self.read_token) as client:
                instr_info = client.instruments.get_instrument_by(id_type=1, id=figi).instrument
                return {
                    "lot_size": instr_info.lot,
                    "min_price_increment": float(quotation_to_decimal(instr_info.min_price_increment)),
                    "qty_step": float(instr_info.lot)
                }
        except Exception as e:
            logging.error(f"Ошибка получения инфо: {e}")
            return {}

    def get_top_liquid_by_turnover(self, count: int) -> List[str]:
        """Запрашивает топ-N ликвидных акций MOEX по обороту за 30 дней."""
        logger.info(f"Tinkoff Client: Запрос топ-{count} ликвидных акций MOEX...")
        try:
            with Client(self.read_token) as client:
                all_shares = client.instruments.shares(
                    instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE).instruments

                tqbr_shares = [
                    s for s in all_shares
                    if s.class_code == 'TQBR' and s.currency == 'rub'
                       and s.trading_status != SecurityTradingStatus.SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING
                ]

                share_data = []
                # Ограничиваем кол-во запросов для ускорения, если нужно,
                # но для точности берем все TQBR
                for share_info in tqdm(tqbr_shares, desc="Анализ оборотов"):
                    try:
                        candles = client.market_data.get_candles(
                            figi=share_info.figi,
                            from_=now() - timedelta(days=30),
                            to=now(),
                            interval=CandleInterval.CANDLE_INTERVAL_DAY
                        ).candles

                        total_turnover = sum(
                            float(quotation_to_decimal(c.close)) * c.volume * share_info.lot
                            for c in candles
                        )

                        if total_turnover > 0:
                            share_data.append({"ticker": share_info.ticker, "turnover": total_turnover})

                        time.sleep(0.05)  # Лимит рейтов

                    except RequestError:
                        continue

                sorted_shares = sorted(share_data, key=lambda x: x['turnover'], reverse=True)
                return [s['ticker'] for s in sorted_shares[:count]]

        except Exception as e:
            logger.error(f"Ошибка при поиске ликвидных инструментов: {e}")
            return []

    def place_market_order(self, instrument_id: str, quantity: float, direction: str, **kwargs):
        """
        Размещает рыночный ордер.
        :param instrument_id: FIGI инструмента
        :param quantity: Количество ЛОТОВ (int)
        :param direction: 'BUY' или 'SELL' (или TradeDirection Enum)
        """
        # Маппинг наших констант на константы Тинькофф
        direction_map = {
            TradeDirection.BUY: OrderDirection.ORDER_DIRECTION_BUY,
            TradeDirection.SELL: OrderDirection.ORDER_DIRECTION_SELL
        }

        # Приводим входной direction (str или Enum) к Enum для поиска в мапе
        # Если пришла строка "BUY", StrEnum позволяет сравнение, но для словаря лучше явный ключ
        dir_key = str(direction).upper()  # "BUY"

        # Пытаемся найти по ключу (если direction это строка) или по значению (если Enum)
        tinkoff_direction = direction_map.get(dir_key)

        if not tinkoff_direction:
            logging.error(f"Неверное направление сделки: {direction}")
            return None

        order_quantity = int(quantity)

        try:
            with Client(self.trade_token) as client:
                if self.trade_mode == "SANDBOX":
                    order = client.sandbox.post_sandbox_order(
                        figi=instrument_id,
                        quantity=order_quantity,
                        order_id=str(now().timestamp()),
                        direction=tinkoff_direction,
                        account_id=self.account_id,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )
                else:  # REAL
                    order = client.orders.post_order(
                        figi=instrument_id,
                        quantity=order_quantity,
                        order_id=str(now().timestamp()),
                        account_id=self.account_id,
                        direction=tinkoff_direction,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )

            logging.info(f"Заявка {dir_key} {order_quantity} лот(ов) {instrument_id} размещена. ID: {order.order_id}")
            return order
        except RequestError as e:
            logging.error(f"Ошибка размещения заявки Tinkoff: {e.details}")
            return None