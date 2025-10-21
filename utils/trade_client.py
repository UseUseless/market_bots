import pandas as pd
from datetime import timedelta
import logging
from tinkoff.invest import Client, RequestError, OrderDirection, OrderType, CandleInterval
from tinkoff.invest.utils import now
from config import TOKEN_REAL, TOKEN_SANDBOX, ACCOUNT_ID

class TinkoffTrader:
    """
    Низкоуровневый клиент для взаимодействия с Tinkoff Invest API.
    Предоставляет простые методы для получения данных и отправки ордеров.
    """
    def __init__(self, trade_mode: str):
        if trade_mode.upper() == "REAL":
            self.token = TOKEN_REAL
            logging.info("Клиент TinkoffTrader настроен для РЕАЛЬНОЙ ТОРГОВЛИ.")
        else:
            self.token = TOKEN_SANDBOX
            logging.info("Клиент TinkoffTrader настроен для ТОРГОВЛИ В ПЕСОЧНИЦЕ.")
        
        if not self.token or "Your" in self.token:
            logging.critical("Токен API не задан или используется токен по умолчанию. Проверьте файл .env")
            exit()
            
        self.account_id = ACCOUNT_ID
        self.client = self._get_client()
        if self.client and not self.account_id:
            self.account_id = self._get_first_account_id()

    def _get_client(self):
        try:
            client = Client(self.token)
            # Пробный запрос для проверки валидности токена
            client.users.get_info()
            logging.info("Клиент TinkoffTrader успешно инициализирован.")
            return client
        except RequestError as e:
            logging.critical(f"Критическая ошибка инициализации клиента Tinkoff: {e}")
            return None

    def _get_first_account_id(self):
        try:
            accounts = self.client.users.get_accounts().accounts
            if not accounts:
                logging.critical("Критическая ошибка: не найдено ни одного счета.")
                exit()
            logging.info(f"Используется счет по умолчанию: {accounts[0].id}")
            return accounts[0].id
        except RequestError as e:
            logging.critical(f"Критическая ошибка получения ID счета: {e}")
            exit()

    def get_historical_data(self, figi: str, days: int, interval_str: str) -> pd.DataFrame:
        if not self.client:
            return pd.DataFrame()
            
        interval_map = {
            "1min": CandleInterval.CANDLE_INTERVAL_1_MIN, "5min": CandleInterval.CANDLE_INTERVAL_5_MIN,
            "15min": CandleInterval.CANDLE_INTERVAL_15_MIN, "1hour": CandleInterval.CANDLE_INTERVAL_HOUR,
            "1day": CandleInterval.CANDLE_INTERVAL_DAY,
        }
        interval = interval_map.get(interval_str)
        if not interval:
            logging.error(f"Неподдерживаемый интервал: {interval_str}")
            return pd.DataFrame()
        
        candles_data = []
        try:
            for candle in self.client.get_all_candles(figi=figi, from_=now() - timedelta(days=days), interval=interval):
                candles_data.append({
                    "time": candle.time, "open": self._cast_money(candle.open),
                    "high": self._cast_money(candle.high), "low": self._cast_money(candle.low),
                    "close": self._cast_money(candle.close), "volume": candle.volume,
                })
        except RequestError as e:
            logging.error(f"Ошибка получения исторических данных для {figi}: {e}")
            return pd.DataFrame()

        df = pd.DataFrame(candles_data)
        if not df.empty:
            df['time'] = pd.to_datetime(df['time'])
        return df

    def place_market_order(self, figi: str, quantity: int, direction: str):
        if not self.client:
            return None
            
        direction_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
        order_direction = direction_map.get(direction.upper())
        if not order_direction:
            logging.error(f"Неверное направление сделки: {direction}")
            return None

        try:
            order = self.client.orders.post_order(
                order_id=str(now().timestamp()), figi=figi, quantity=quantity,
                account_id=self.account_id, direction=order_direction, order_type=OrderType.ORDER_TYPE_MARKET,
            )
            logging.info(f"Заявка {direction} {quantity} лот(ов) {figi} успешно размещена. Order ID: {order.order_id}")
            return order
        except RequestError as e:
            logging.error(f"Ошибка размещения заявки: {e}")
            return None

    @staticmethod
    def _cast_money(money_value):
        """Конвертирует внутренний формат денег Tinkoff API в float."""
        return money_value.units + money_value.nano / 1e9