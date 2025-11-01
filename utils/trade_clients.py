import logging
from abc import ABC, abstractmethod
from typing import Literal

# --- Библиотеки для Tinkoff ---
from tinkoff.invest import Client, RequestError, OrderDirection, OrderType
from tinkoff.invest.utils import now
from config import TOKEN_FULL_ACCESS, TOKEN_SANDBOX, ACCOUNT_ID

# --- Библиотеки для Bybit (на будущее) ---
# from pybit.unified_trading import HTTP

# Тип для выбора торгового режима
TradeModeType = Literal["REAL", "SANDBOX"]


# --- Абстрактный базовый класс для всех торговых клиентов ---

class BaseTradeClient(ABC):
    """Абстрактный 'контракт' для всех клиентов, исполняющих ордера."""

    @abstractmethod
    def place_market_order(self, instrument: str, quantity: int, direction: str):
        """Размещает рыночный ордер."""
        raise NotImplementedError


# --- Торговый клиент для Tinkoff ---

class TinkoffTradeClient(BaseTradeClient):
    """Клиент для исполнения ордеров через Tinkoff Invest API."""

    def __init__(self, trade_mode: TradeModeType):
        self.trade_mode = trade_mode.upper()
        self.trade_token: str | None = None
        self.account_id = ACCOUNT_ID

        if self.trade_mode == "REAL":
            self.trade_token = TOKEN_FULL_ACCESS
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен с полным доступом (TOKEN_FULL_ACCESS) не задан в .env.")
            if not self.account_id:
                logging.info("ID реального счета не указан, будет использован первый доступный.")
                self.account_id = self._get_first_account_id()
        elif self.trade_mode == "SANDBOX":
            self.trade_token = TOKEN_SANDBOX
            if not self.trade_token or "Your" in self.trade_token:
                raise ConnectionError("Токен песочницы (TOKEN_SANDBOX) не задан в .env.")
        else:
            raise ValueError(f"Неподдерживаемый торговый режим: {trade_mode}")

        logging.info(f"Торговый клиент Tinkoff инициализирован в режиме '{self.trade_mode}'.")

    def _get_first_account_id(self) -> str:
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

    def place_market_order(self, instrument: str, quantity: int, direction: str):
        """Размещает рыночный ордер. instrument должен быть FIGI."""
        direction_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
        order_direction = direction_map.get(direction.upper())
        if not order_direction:
            logging.error(f"Неверное направление сделки: {direction}")
            return None

        try:
            with Client(self.trade_token) as client:
                if self.trade_mode == "SANDBOX":
                    sandbox_accounts = client.sandbox.get_sandbox_accounts().accounts
                    if not sandbox_accounts:
                        raise ConnectionError("Не найдено счетов в песочнице.")
                    account_id = sandbox_accounts[0].id

                    order = client.sandbox.post_sandbox_order(
                        instrument=instrument, quantity=quantity, order_id=str(now().timestamp()),
                        direction=order_direction, account_id=account_id,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )
                else:  # REAL
                    if not self.account_id:
                        raise ValueError("Невозможно разместить реальный ордер: не определен ID счета.")

                    order = client.orders.post_order(
                        instrument=instrument, quantity=quantity, order_id=str(now().timestamp()),
                        account_id=self.account_id, direction=order_direction,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )

            logging.info(
                f"Заявка {direction} {quantity} лот(ов) {instrument} в режиме '{self.trade_mode}' успешно размещена. Order ID: {order.order_id}")
            return order
        except RequestError as e:
            logging.error(f"Ошибка размещения заявки в режиме '{self.trade_mode}': {e}")
            return None


# --- Торговый клиент для Bybit (заготовка на будущее) ---

class BybitTradeClient(BaseTradeClient):
    def __init__(self, trade_mode: TradeModeType):
        # Здесь будет логика инициализации с API ключами из .env
        logging.info(f"Торговый клиент Bybit инициализирован в режиме '{trade_mode}'.")
        pass

    def place_market_order(self, instrument: str, quantity: int, direction: str):
        # Здесь будет логика отправки ордера через pybit
        logging.info(f"Отправка ордера на Bybit: {direction} {quantity} {instrument}")
        pass