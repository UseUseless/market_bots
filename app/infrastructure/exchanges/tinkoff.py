"""
Клиент для взаимодействия с Tinkoff Invest API.

Этот модуль реализует интерфейсы для получения исторических данных,
информации об инструментах и исполнения торговых ордеров на Московской бирже (через Тинькофф).
Поддерживает работу в Песочнице (Sandbox) и на реальном счете.

Использует библиотеку `tinkoff.invest`.
"""

import logging
import time
from datetime import timedelta
from typing import List, Dict, Any, Optional
from tqdm import tqdm
import pandas as pd

from tinkoff.invest import (
    Client, RequestError,
    CandleInterval, InstrumentStatus,
    OrderDirection, OrderType
)
from tinkoff.invest.utils import now, quotation_to_decimal

from app.core.interfaces import TradeModeType, BaseDataClient, BaseTradeClient
from app.shared.primitives import TradeDirection, ExchangeType
from app.shared.config import config

logger = logging.getLogger(__name__)


class TinkoffHandler(BaseDataClient, BaseTradeClient):
    """
    Адаптер для биржи Tinkoff Invest.

    Объединяет функциональность Data Client (история) и Trade Client (исполнение).
    """

    def __init__(self, trade_mode: TradeModeType = "SANDBOX"):
        """
        Инициализирует клиента Tinkoff.

        Автоматически выбирает нужный токен (ReadOnly, FullAccess или Sandbox)
        в зависимости от режима работы. Если в режиме Sandbox нет открытых счетов,
        клиент попытается открыть новый.

        Args:
            trade_mode (TradeModeType): Режим работы ('SANDBOX' или 'REAL').
        """
        self.read_token = config.TINKOFF_TOKEN_READONLY
        self.trade_mode = trade_mode.upper()
        self.trade_token: Optional[str] = None

        # Единое хранилище ID счета (и для Real, и для Sandbox)
        self.account_id: Optional[str] = config.TINKOFF_ACCOUNT_ID

        # 1. Проверка токена для чтения (обязателен всегда)
        if not self.read_token or "Your" in self.read_token:
            raise ConnectionError("Токен только для чтения (TOKEN_READONLY) не задан в .env.")

        # 2. Настройка торгового токена и счета
        if self.trade_mode == "REAL":
            self.trade_token = config.TINKOFF_TOKEN_FULL_ACCESS
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен с полным доступом (TOKEN_FULL_ACCESS) не задан в .env.")

            is_sandbox = False

        elif self.trade_mode == "SANDBOX":
            self.trade_token = config.TINKOFF_TOKEN_SANDBOX
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен песочницы (TOKEN_SANDBOX) не задан в .env.")

            is_sandbox = True
        else:
            raise ValueError(f"Неподдерживаемый торговый режим: {trade_mode}")

        # 3. Автоматический поиск или создание счета, если ID не задан явно
        if not self.account_id:
            logging.info(f"ID счета не указан в конфиге. Поиск первого доступного счета ({self.trade_mode})...")
            self.account_id = self._get_first_account_id(sandbox=is_sandbox)
            logging.info(f"Выбран счет: {self.account_id}")

        logging.info(f"Торговый клиент Tinkoff инициализирован ({self.trade_mode}).")
        self._check_token()

    def _check_token(self) -> bool:
        """Проверяет валидность токена ReadOnly простым запросом."""
        try:
            with Client(self.read_token) as client:
                client.users.get_accounts()
            logging.info("Токен Tinkoff 'только для чтения' валиден.")
            return True
        except RequestError as e:
            logging.critical(f"Ошибка проверки токена Tinkoff: {e}")
            raise ConnectionAbortedError(f"Невалидный токен Tinkoff: {e.details}")

    def _get_first_account_id(self, sandbox: bool = False) -> str:
        """
        Получает ID первого доступного счета.
        В режиме Sandbox создает новый счет, если список пуст.
        """
        try:
            with Client(self.trade_token) as client:
                if sandbox:
                    service = client.sandbox
                    accounts = service.get_sandbox_accounts().accounts
                else:
                    service = client.users
                    accounts = service.get_accounts().accounts

                if not accounts:
                    if sandbox:
                        logging.info("Счета в песочнице не найдены. Открываем новый...")
                        service.open_sandbox_account()
                        # Повторный запрос после открытия
                        accounts = service.get_sandbox_accounts().accounts

                    if not accounts:
                        raise ConnectionError(f"Не найдено счетов в режиме {'SANDBOX' if sandbox else 'REAL'}.")

                return accounts[0].id
        except RequestError as e:
            logging.critical(f"Критическая ошибка получения ID счета: {e}")
            raise

    def _resolve_figi(self, instrument: str) -> str:
        """
        Находит уникальный идентификатор (FIGI) по тикеру инструмента.

        Использует двухступенчатый поиск:
        1. Строгий поиск: Тикер + Класс (например, TQBR для акций).
        2. Мягкий поиск: Первый попавшийся инструмент с таким тикером.

        Args:
            instrument (str): Тикер (например, SBER).

        Returns:
            str: FIGI инструмента.
        """
        # Если передан уже FIGI (начинается с BBG), возвращаем как есть
        if instrument.startswith("BBG"):
            return instrument

        logger.info(f"Поиск FIGI для тикера '{instrument}'...")

        with Client(self.read_token) as c:
            try:
                found = c.instruments.find_instrument(query=instrument)
                if not found.instruments:
                    raise ValueError(f"Инструмент '{instrument}' не найден через API.")

                instrument_upper = instrument.upper()
                class_code = config.EXCHANGE_SPECIFIC_CONFIG[ExchangeType.TINKOFF]['DEFAULT_CLASS_CODE']

                # 1. Строгий поиск по классу (акции на Мосбирже)
                strict_match = next((
                    instr for instr in found.instruments
                    if instr.ticker == instrument_upper and instr.class_code == class_code
                ), None)

                if strict_match:
                    target_instrument = strict_match
                else:
                    # 2. Мягкий поиск (любое совпадение по тикеру)
                    logger.warning(
                        f"Строгое совпадение для '{instrument_upper}' (class={class_code}) не найдено. Ищем по тикеру.")
                    exact_match = next((instr for instr in found.instruments if instr.ticker == instrument_upper), None)
                    # Если совсем ничего похожего, берем первый результат поиска
                    target_instrument = exact_match if exact_match else found.instruments[0]

                logger.info(f"Выбран: '{target_instrument.name}' (FIGI: {target_instrument.figi})")
                return target_instrument.figi

            except RequestError as e:
                logger.error(f"Ошибка API при поиске '{instrument}': {e}")
                raise

    @staticmethod
    def _cast_money(money_value) -> float:
        """Конвертирует структуру MoneyValue/Quotation в float."""
        return money_value.units + money_value.nano / 1e9

    def get_historical_data(self, instrument: str, interval: str, days: int, **kwargs) -> pd.DataFrame:
        """
        Скачивает исторические свечи.

        Args:
            instrument (str): Тикер инструмента.
            interval (str): Интервал (1min, 5min, 1hour, etc).
            days (int): Глубина истории.

        Returns:
            pd.DataFrame: DataFrame с колонками time, open, high, low, close, volume.
        """
        try:
            figi = self._resolve_figi(instrument)
        except (ValueError, RequestError) as e:
            logging.error(f"FIGI error for '{instrument}': {e}")
            return pd.DataFrame()

        interval_name = config.EXCHANGE_INTERVAL_MAPS[ExchangeType.TINKOFF].get(interval)
        if not interval_name:
            logging.error(f"Неподдерживаемый интервал: {interval}")
            return pd.DataFrame()

        # Получаем Enum значение интервала из библиотеки tinkoff
        api_interval = getattr(CandleInterval, interval_name)

        all_candles = []
        start_date = now() - timedelta(days=days)
        print(f"Запрос данных Tinkoff для {instrument} ({figi}) с {start_date.date()}...")

        try:
            with Client(self.read_token) as c, tqdm(total=days, desc="Загрузка", unit="дн.") as pbar:
                # Метод get_all_candles сам обрабатывает пагинацию
                for candle in c.get_all_candles(figi=figi, from_=start_date, interval=api_interval):

                    # Обновление прогресс-бара
                    current_progress_days = (candle.time.date() - start_date.date()).days
                    if current_progress_days > pbar.n:
                        pbar.update(current_progress_days - pbar.n)

                    all_candles.append({
                        "time": candle.time,
                        "open": self._cast_money(candle.open),
                        "high": self._cast_money(candle.high),
                        "low": self._cast_money(candle.low),
                        "close": self._cast_money(candle.close),
                        "volume": candle.volume
                    })

                # Добиваем прогресс бар до конца
                if pbar.n < days:
                    pbar.update(days - pbar.n)

        except RequestError as e:
            logging.error(f"Ошибка API при получении данных: {e.details}")
            return pd.DataFrame()

        df = pd.DataFrame(all_candles)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'], utc=True)
        return df

    def get_instrument_info(self, instrument: str, **kwargs) -> Dict[str, Any]:
        """
        Получает параметры инструмента (шаг цены, лотность).
        """
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
        """
        Возвращает список самых ликвидных акций (TQBR) по обороту за последние дни.
        Используется для формирования списков для скринера.
        """
        logger.info(f"Tinkoff Client: Анализ ликвидности акций MOEX (TQBR)...")
        try:
            with Client(self.read_token) as client:
                # 1. Получаем список всех доступных акций
                all_shares = client.instruments.shares(
                    instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE
                ).instruments

                # 2. Фильтруем: только TQBR (Мосбиржа), рубли и доступные для торгов
                tqbr_shares = [
                    s for s in all_shares
                    if s.class_code == 'TQBR'
                       and s.currency == 'rub'
                       and s.buy_available_flag
                       and s.api_trade_available_flag
                ]

                logger.info(f"Найдено {len(tqbr_shares)} активных акций TQBR. Расчет оборота...")

                share_stats = []
                interval_start = now() - timedelta(days=2)  # Берем последние 2 дня
                interval_end = now()

                for share in tqdm(tqbr_shares, desc="Сканирование Tinkoff", unit="ticker"):
                    try:
                        candles = client.market_data.get_candles(
                            figi=share.figi,
                            from_=interval_start,
                            to=interval_end,
                            interval=CandleInterval.CANDLE_INTERVAL_DAY
                        ).candles

                        if not candles:
                            continue

                        # Расчет оборота: сумма (Close * Volume * Lot) по всем свечам
                        total_turnover = sum(
                            self._cast_money(c.close) * c.volume * share.lot
                            for c in candles
                        )

                        if total_turnover > 0:
                            share_stats.append({
                                "ticker": share.ticker,
                                "turnover": total_turnover
                            })

                        # Небольшая задержка, чтобы не превысить лимиты API
                        time.sleep(0.1)

                    except RequestError:
                        continue  # Игнорируем ошибки по конкретному тикеру
                    except Exception as e:
                        # Ловим только технические ошибки, но не прерываем весь цикл
                        logger.debug(f"Ошибка обработки {share.ticker}: {e}")
                        continue

                # Сортировка по убыванию оборота
                sorted_shares = sorted(share_stats, key=lambda x: x['turnover'], reverse=True)
                top_result = [s['ticker'] for s in sorted_shares[:count]]

                logger.info(f"Топ-{count} Tinkoff: {top_result}")
                return top_result

        except Exception as e:
            logger.error(f"Критическая ошибка Tinkoff get_top_liquid: {e}")
            return []

    def place_market_order(self, instrument_id: str, quantity: float, direction: str, **kwargs) -> Optional[Any]:
        """
        Размещает рыночный ордер.

        Args:
            instrument_id (str): FIGI инструмента (Tinkoff использует FIGI, не тикеры).
            quantity (float): Количество лотов.
            direction (str): Направление ('BUY' или 'SELL').

        Returns:
            Optional[Any]: Объект ответа API (PostOrderResponse) или None в случае ошибки.
        """
        direction_map = {
            TradeDirection.BUY: OrderDirection.ORDER_DIRECTION_BUY,
            TradeDirection.SELL: OrderDirection.ORDER_DIRECTION_SELL
        }

        # Приведение к верхнему регистру для надежности
        dir_key = str(direction).upper()
        tinkoff_direction = direction_map.get(dir_key)

        if not tinkoff_direction:
            logging.error(f"Неверное направление сделки: {direction}")
            return None

        # Tinkoff API ожидает int для лотов
        order_quantity = int(quantity)

        try:
            with Client(self.trade_token) as client:
                # В SDK методы для Sandbox и Real отличаются неймспейсами (sandbox vs orders)
                if self.trade_mode == "SANDBOX":
                    order = client.sandbox.post_sandbox_order(
                        figi=instrument_id,
                        quantity=order_quantity,
                        order_id=str(now().timestamp()),  # Уникальный ID запроса (идемпотентность)
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