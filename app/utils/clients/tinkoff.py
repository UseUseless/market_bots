import logging
import time
from datetime import timedelta
from typing import List
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
from config import (
    TOKEN_READONLY, TOKEN_FULL_ACCESS,
    TOKEN_SANDBOX, ACCOUNT_ID,
    EXCHANGE_SPECIFIC_CONFIG,EXCHANGE_INTERVAL_MAPS
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
        self.account_id = ACCOUNT_ID

        if not self.read_token or "Your" in self.read_token:
            raise ConnectionError("Токен только для чтения (TOKEN_READONLY) не задан в .env.")

        if self.trade_mode == "REAL":
            self.trade_token = TOKEN_FULL_ACCESS
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен с полным доступом (TOKEN_FULL_ACCESS) не задан в .env.")
            if not self.account_id:
                logging.info("ID реального счета не указан, будет использован первый доступный.")
                self.account_id = self._get_first_real_account_id()
        elif self.trade_mode == "SANDBOX":
            self.trade_token = TOKEN_SANDBOX
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен песочницы (TOKEN_SANDBOX) не задан в .env.")
        else:
            raise ValueError(f"Неподдерживаемый торговый режим: {trade_mode}")

        logging.info(f"Торговый клиент Tinkoff инициализирован в режиме '{self.trade_mode}'.")

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

    def _get_first_real_account_id(self) -> str:
        """Приватный метод для получения ID первого доступного реального счета."""
        try:
            with Client(self.trade_token) as client:
                accounts = client.users.get_accounts().accounts
                if not accounts:
                    raise ConnectionError("Критическая ошибка: не найдено ни одного реального счета.")
                account_id = accounts[0].id
                logging.info(f"Используется реальный счет по умолчанию: {account_id}")
                return account_id
        except RequestError as e:
            logging.critical(f"Критическая ошибка получения ID реального счета: {e}")
            raise

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

    @staticmethod
    def _cast_money(money_value) -> float:
        return money_value.units + money_value.nano / 1e9

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

    def place_market_order(self, instrument_id: str, quantity: float, direction: str, **kwargs):
        """Размещает рыночный ордер. instrument_id должен быть FIGI."""
        direction_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
        order_direction = direction_map.get(direction.upper())
        if not order_direction:
            logging.error(f"Неверное направление сделки: {direction}")
            return None

        order_quantity = int(quantity)

        try:
            with Client(self.trade_token) as client:
                if self.trade_mode == "SANDBOX":
                    sandbox_accounts = client.sandbox.get_sandbox_accounts().accounts
                    if not sandbox_accounts:
                        raise ConnectionError("Не найдено счетов в песочнице.")
                    account_id = sandbox_accounts[0].id

                    order = client.sandbox.post_sandbox_order(
                        figi=instrument_id, quantity=order_quantity, order_id=str(now().timestamp()),
                        direction=order_direction, account_id=account_id,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )
                else:  # REAL
                    if not self.account_id:
                        raise ValueError("Невозможно разместить реальный ордер: не определен ID счета.")

                    order = client.orders.post_order(
                        figi=instrument_id, quantity=order_quantity, order_id=str(now().timestamp()),
                        account_id=self.account_id, direction=order_direction,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )

            logging.info(
                f"Заявка {direction} {order_quantity} лот(ов) {instrument_id} успешно размещена. Order ID: {order.order_id}")
            return order
        except RequestError as e:
            logging.error(f"Ошибка размещения заявки Tinkoff: {e.details}")
            return None