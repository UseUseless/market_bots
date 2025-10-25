import pandas as pd
from datetime import timedelta
import logging
from tinkoff.invest import Client, RequestError, OrderDirection, OrderType, CandleInterval
from tinkoff.invest.utils import now
from config import TOKEN_REAL, TOKEN_SANDBOX, ACCOUNT_ID

class TinkoffTrader:
    """
    Низкоуровневый клиент для взаимодействия с Tinkoff Invest API.
    """
    def __init__(self, trade_mode: str):
        # Для чтения данных (история, счета) всегда нужен боевой токен.
        # Рекомендуется использовать токен с правами "только для чтения".
        self.read_token = TOKEN_REAL
        
        # Для торговых операций токен зависит от режима.
        self.trade_mode = trade_mode.upper()
        self.trade_token = TOKEN_REAL if self.trade_mode == "REAL" else TOKEN_SANDBOX
        
        if not self.read_token or "Your" in self.read_token:
            logging.critical("Боевой токен (TOKEN_REAL) не задан в .env. Он необходим для чтения данных.")
            exit()
            
        self.account_id = ACCOUNT_ID
        if not self._check_token():
            logging.critical("Не удалось выполнить тестовый запрос к API с помощью TOKEN_REAL. Проверьте токен и сетевое соединение.")
            exit()
            
        if self.trade_mode == "REAL" and not self.account_id:
            logging.info("ID реального счета (TINKOFF_ACCOUNT_ID) не указан. Будет использован первый доступный счет.")
            self.account_id = self._get_first_account_id()

    def _check_token(self) -> bool:
        """Проверяет валидность боевого токена, делая тестовый запрос на получение счетов."""
        try:
            with Client(self.read_token) as client:
                client.users.get_accounts()
            logging.info("Боевой токен (read-only) успешно прошел проверку.")
            return True
        except RequestError as e:
            logging.critical(f"Ошибка проверки боевого токена: {e}")
            return False

    def _get_first_account_id(self):
        """Получает ID первого доступного реального счета."""
        try:
            with Client(self.read_token) as client:
                accounts = client.users.get_accounts().accounts
                if not accounts:
                    logging.critical("Критическая ошибка: не найдено ни одного реального счета.")
                    return None
                account_id = accounts[0].id
                logging.info(f"Используется реальный счет по умолчанию: {account_id}")
                return account_id
        except RequestError as e:
            logging.critical(f"Критическая ошибка получения ID реального счета: {e}")
            return None

    def get_historical_data(self, figi: str, days: int, interval_str: str) -> pd.DataFrame:
        """Для получения истории всегда используется боевой токен."""
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
            with Client(self.read_token) as client:
                for candle in client.get_all_candles(figi=figi, from_=now() - timedelta(days=days), interval=interval):
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
        """Размещает рыночный ордер в зависимости от установленного trade_mode."""
        direction_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}
        order_direction = direction_map.get(direction.upper())
        if not order_direction:
            logging.error(f"Неверное направление сделки: {direction}")
            return None

        try:
            if self.trade_mode == "SANDBOX":
                with Client(self.trade_token) as client:
                    sandbox_accounts = client.sandbox.get_sandbox_accounts().accounts
                    if not sandbox_accounts:
                        logging.error("Не найдено счетов в песочнице для совершения сделки.")
                        return None
                    sandbox_account_id = sandbox_accounts[0].id
                    
                    order = client.sandbox.post_sandbox_order(
                        figi=figi,
                        quantity=quantity,
                        order_id=str(now().timestamp()),
                        direction=order_direction,
                        account_id=sandbox_account_id,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )
            else: # REAL
                if not self.account_id:
                    logging.error("Невозможно разместить реальный ордер: не определен ID счета.")
                    return None
                with Client(self.trade_token) as client:
                    order = client.orders.post_order(
                        figi=figi,
                        quantity=quantity,
                        order_id=str(now().timestamp()),
                        account_id=self.account_id,
                        direction=order_direction,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )
            
            logging.info(f"Заявка {direction} {quantity} лот(ов) {figi} в режиме '{self.trade_mode}' успешно размещена. Order ID: {order.order_id}")
            return order
        except RequestError as e:
            logging.error(f"Ошибка размещения заявки в режиме '{self.trade_mode}': {e}")
            return None

    @staticmethod
    def _cast_money(money_value):
        """Конвертирует внутренний формат денег Tinkoff API в float."""
        return money_value.units + money_value.nano / 1e9