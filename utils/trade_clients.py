import logging
import os
from abc import ABC, abstractmethod
from typing import Literal

# --- Библиотеки для Tinkoff ---
from tinkoff.invest import Client, RequestError, OrderDirection, OrderType
from tinkoff.invest.utils import now
from config import TOKEN_FULL_ACCESS, TOKEN_SANDBOX, ACCOUNT_ID, BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET

# --- Библиотеки для Bybit ---
from pybit.unified_trading import HTTP

# Тип для выбора торгового режима
TradeModeType = Literal["REAL", "SANDBOX"]


# --- Абстрактный базовый класс для всех торговых клиентов ---

class BaseTradeClient(ABC):
    """Абстрактный 'контракт' для всех клиентов, исполняющих ордера."""

    @abstractmethod
    def place_market_order(self, instrument_id: str, quantity: int, direction: str):
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

    def place_market_order(self, instrument_id: str, quantity: int, direction: str):
        """Размещает рыночный ордер. instrument_id должен быть FIGI."""
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
                        figi=instrument_id, quantity=quantity, order_id=str(now().timestamp()),
                        direction=order_direction, account_id=account_id,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )
                else:  # REAL
                    if not self.account_id:
                        raise ValueError("Невозможно разместить реальный ордер: не определен ID счета.")

                    order = client.orders.post_order(
                        figi=instrument_id, quantity=quantity, order_id=str(now().timestamp()),
                        account_id=self.account_id, direction=order_direction,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )

            logging.info(
                f"Заявка {direction} {quantity} лот(ов) {instrument_id} в режиме '{self.trade_mode}' успешно размещена. Order ID: {order.order_id}")
            return order
        except RequestError as e:
            logging.error(f"Ошибка размещения заявки в режиме '{self.trade_mode}': {e}")
            return None


# --- Торговый клиент для Bybit (заготовка на будущее) ---

class BybitTradeClient(BaseTradeClient):
    def __init__(self, trade_mode: TradeModeType):
        use_testnet = (trade_mode == "SANDBOX")
        # TODO: Добавить ключи для реальной торговли в .env и search_space.py
        api_key = BYBIT_TESTNET_API_KEY if use_testnet else 'Нет_ничего. Тут будет реальные ключи'
        api_secret = BYBIT_TESTNET_API_SECRET if use_testnet else 'Нет_ничего. Тут будет реальные ключи'

        if not api_key or not api_secret:
            raise ConnectionError(f"API ключи для Bybit ({trade_mode}) не заданы в .env.")

        self.client = HTTP(
            testnet=use_testnet,
            api_key=api_key,
            api_secret=api_secret
        )
        logging.info(f"Торговый клиент Bybit инициализирован в режиме '{trade_mode}'.")

    def place_market_order(self, instrument_id: str, quantity: int, direction: str):
        logging.info(f"Отправка ордера на Bybit: {direction} {quantity} {instrument_id}")
        # pybit ожидает qty как строку
        response = self.client.place_order(
            category="linear",
            symbol=instrument_id,
            side=direction.capitalize(),
            orderType="Market",
            qty=str(quantity)
        )
        logging.info(f"Ответ от Bybit: {response}")
        return response