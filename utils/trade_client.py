import pandas as pd
from datetime import timedelta
import logging
from typing import Literal
from tinkoff.invest import Client, RequestError, OrderDirection, OrderType, CandleInterval
from tinkoff.invest.utils import now
from config import TOKEN_READONLY, TOKEN_FULL_ACCESS, TOKEN_SANDBOX, ACCOUNT_ID

# Этот тип описывает все возможные торговые режимы.
TradeModeType = Literal["REAL", "SANDBOX"]
# Этот тип описывает все валидные интервалы свечей.
IntervalType = Literal["1min", "5min", "15min", "1hour", "1day"]

class TinkoffTrader:
    """
    Клиент для взаимодействия с Tinkoff Invest API.
    По умолчанию работает в безопасном режиме "только для чтения".
    Торговый функционал (real/sandbox) активируется явной передачей `trade_mode`.
    """
    def __init__(self, trade_mode: TradeModeType | None = None):
        # Клиент ВСЕГДА инициализируется с токеном "только для чтения".
        self.read_token = TOKEN_READONLY
        # Определяем запрошенный торговый режим (если он есть), приводя его к верхнему регистру.
        self.trade_mode = trade_mode.upper() if trade_mode else None
        self.trade_token = None
        self.account_id = ACCOUNT_ID

        # Проверяем задан ли TOKEN_READONLY в .env
        if not self.read_token or "Your" in self.read_token:
            logging.critical("Токен только для чтения (TOKEN_READONLY) не задан в .env.")
            exit()

        # Проверяем, что токен не просто задан, а он валидный (API его принимает и не выдаёт ошибку).
        if not self._check_token():
            logging.critical("Не удалось выполнить тестовый запрос с помощью TOKEN_READONLY.")
            exit()

        # --- Активация торгового режима (если он был запрошен) ---
        if self.trade_mode == "REAL":
            self.trade_token = TOKEN_FULL_ACCESS
            # Проверяем, что этот токен задан в .env
            if not self.trade_token or "Your" in self.trade_token:
                logging.critical("Токен с полным доступом (TOKEN_FULL_ACCESS) не задан в .env для реальной торговли.")
                exit()
            # Если ID счета не указан, пытаемся получить его автоматически
            if not self.account_id:
                logging.info("ID реального счета не указан, будет использован первый доступный.")
                self.account_id = self._get_first_account_id()

        # Если пользователь явно запросил режим песочницы
        elif self.trade_mode == "SANDBOX":
            self.trade_token = TOKEN_SANDBOX
            # Проверяем, что он задан в .env
            if not self.trade_token or "Your" in self.trade_token:
                logging.critical("Токен песочницы (TOKEN_SANDBOX) не задан в .env для торговли в песочнице.")
                exit()

    def _check_token(self) -> bool:
        """Приватный метод для проверки валидности токена 'только для чтения'."""
        try:
            with Client(self.read_token) as client:
                # Делаем самый простой запрос, который требует аутентификации - получение списка счетов.
                client.users.get_accounts()
            logging.info("Токен 'только для чтения' успешно прошел проверку.")
            return True
        except RequestError as e:
            logging.critical(f"Ошибка проверки токена 'только для чтения': {e}")
            return False

    def _get_first_account_id(self):
        """Приватный метод для получения ID первого доступного реального счета."""
        try:
            with Client(self.read_token) as client:
                accounts = client.users.get_accounts().accounts
                if not accounts:
                    logging.critical("Критическая ошибка: не найдено ни одного реального счета.")
                    return None
                # Берем ID самого первого счета из списка
                # ToDo: Нужно понять для чего эти счета и дать пользователю выбирать вопросом в консоли
                account_id = accounts[0].id
                logging.info(f"Используется реальный счет по умолчанию: {account_id}")
                return account_id
        except RequestError as e:
            logging.critical(f"Критическая ошибка получения ID реального счета: {e}")
            return None

    def get_historical_data(self, figi: str, days: int, interval_str: IntervalType) -> pd.DataFrame:
        """
        Получает исторические свечные данные за указанный период.
        Для этой операции всегда используется безопасный токен 'только для чтения'.
        """
        # Словарь для преобразования нашей строки-интервала (напр., "5min")
        # в специальный формат, который понимает Tinkoff API.
        interval_map = {
            "1min": CandleInterval.CANDLE_INTERVAL_1_MIN, "5min": CandleInterval.CANDLE_INTERVAL_5_MIN,
            "15min": CandleInterval.CANDLE_INTERVAL_15_MIN, "1hour": CandleInterval.CANDLE_INTERVAL_HOUR,
            "1day": CandleInterval.CANDLE_INTERVAL_DAY,
        }
        interval = interval_map.get(interval_str)
        if not interval:
            logging.error(f"Неподдерживаемый интервал: {interval_str}")
            return pd.DataFrame() # Возвращаем пустой DataFrame в случае ошибки
        
        candles_data = []
        try:
            with Client(self.read_token) as client:
                # client.get_all_candles - это удобная функция-генератор из библиотеки,
                # которая сама обрабатывает "склейку" данных, если их нужно запросить несколькими частями.
                for candle in client.get_all_candles(figi=figi, from_=now() - timedelta(days=days), interval=interval):
                    # Собираем данные в список словарей, приводя денежный формат Tinkoff к обычному float
                    candles_data.append({
                        "time": candle.time, "open": self._cast_money(candle.open),
                        "high": self._cast_money(candle.high), "low": self._cast_money(candle.low),
                        "close": self._cast_money(candle.close), "volume": candle.volume,
                    })
        except RequestError as e:
            logging.error(f"Ошибка получения исторических данных для {figi}: {e}")
            return pd.DataFrame()

        # Превращаем список словарей в DataFrame
        df = pd.DataFrame(candles_data)
        if not df.empty:
            # Убеждаемся, что колонка 'time' имеет правильный тип данных
            df['time'] = pd.to_datetime(df['time'])
        return df

    def place_market_order(self, figi: str, quantity: int, direction: str):
        """
        Задел на будущее
        Часть кода уже в execution закомментирована
        Размещает рыночный ордер (покупает или продает по текущей рыночной цене).
        Метод вызовет ошибку, если клиент был создан без `trade_mode`.
        """
        # Если клиент был создан как TinkoffTrader(), то self.trade_mode будет None.
        if not self.trade_mode:
            logging.error("Попытка совершить сделку на клиенте, созданном в режиме 'только для чтения'.")
            return None

        # Преобразование строки "BUY" / "SELL" в формат Tinkoff API
        direction_map = {"BUY": OrderDirection.ORDER_DIRECTION_BUY, "SELL": OrderDirection.ORDER_DIRECTION_SELL}

        order_direction = direction_map.get(direction.upper())
        if not order_direction:
            logging.error(f"Неверное направление сделки: {direction}")
            return None

        try:
            if self.trade_mode == "SANDBOX":
                with Client(self.trade_token) as client:
                    # Получаем ID своего виртуального счета
                    sandbox_accounts = client.sandbox.get_sandbox_accounts().accounts
                    if not sandbox_accounts:
                        logging.error("Не найдено счетов в песочнице для совершения сделки.")
                        return None
                    sandbox_account_id = sandbox_accounts[0].id

                    # Отправляем ордер через специальный метод для песочницы
                    order = client.sandbox.post_sandbox_order(
                        figi=figi,
                        quantity=quantity,
                        order_id=str(now().timestamp()),
                        direction=order_direction,
                        account_id=sandbox_account_id,
                        order_type=OrderType.ORDER_TYPE_MARKET,
                    )
            else: # self.trade_mode == "REAL"
                if not self.account_id:
                    logging.error("Невозможно разместить реальный ордер: не определен ID счета.")
                    return None
                with Client(self.trade_token) as client:
                    # Отправляем ордер через боевой метод
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