import pandas as pd
from datetime import timedelta
import logging
from typing import Literal
from tqdm import tqdm

from grpc import StatusCode
from tinkoff.invest import Client, RequestError, OrderDirection, OrderType, CandleInterval
from tinkoff.invest.utils import now
from config import TOKEN_READONLY, TOKEN_FULL_ACCESS, TOKEN_SANDBOX, ACCOUNT_ID

# Этот тип описывает все возможные торговые режимы.
TradeModeType = Literal["REAL", "SANDBOX"]
# Этот тип описывает все валидные интервалы свечей.
IntervalType = Literal[
    "1min",
    "2min",
    "3min",
    "5min",
    "10min",
    "15min",
    "30min",
    "1hour",
    "2hour",
    "4hour",
    "1day",
    "1week",
    "1month",
]

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
            "1min": CandleInterval.CANDLE_INTERVAL_1_MIN,
            "2min": CandleInterval.CANDLE_INTERVAL_2_MIN,
            "3min": CandleInterval.CANDLE_INTERVAL_3_MIN,
            "5min": CandleInterval.CANDLE_INTERVAL_5_MIN,
            "10min": CandleInterval.CANDLE_INTERVAL_10_MIN,
            "15min": CandleInterval.CANDLE_INTERVAL_15_MIN,
            "30min": CandleInterval.CANDLE_INTERVAL_30_MIN,
            "1hour": CandleInterval.CANDLE_INTERVAL_HOUR,
            "2hour": CandleInterval.CANDLE_INTERVAL_2_HOUR,
            "4hour": CandleInterval.CANDLE_INTERVAL_4_HOUR,
            "1day": CandleInterval.CANDLE_INTERVAL_DAY,
            "1week": CandleInterval.CANDLE_INTERVAL_WEEK,
            "1month": CandleInterval.CANDLE_INTERVAL_MONTH,
        }
        interval = interval_map.get(interval_str)
        if not interval:
            logging.error(f"Неподдерживаемый интервал: {interval_str}")
            return pd.DataFrame() # Возвращаем пустой DataFrame в случае ошибки

        all_candles = []
        start_date = now() - timedelta(days=days)

        print(f"Запрос данных для {figi} с {start_date.date()}...")

        try:
            with Client(self.read_token) as client:

                # Создаем индикатор прогресса. total=days задает "цель" в 100%.
                # desc - это текст, который будет отображаться слева от полосы.
                with tqdm(total=days, desc="Прогресс загрузки", unit="дн.", colour="green") as pbar:
                    # client.get_all_candles - это удобная функция-генератор из библиотеки,
                    # которая сама обрабатывает "склейку" данных, если их нужно запросить несколькими частями.
                    for candle in client.get_all_candles(figi=figi, from_=start_date, interval=interval):
                        # Рассчитываем, сколько дней прошло от начала запроса до текущей свечи
                        current_progress_days = (candle.time.date() - start_date.date()).days
                        # Обновляем индикатор. `pbar.n` - текущее значение.
                        if current_progress_days > pbar.n:
                            pbar.update(current_progress_days - pbar.n)

                        all_candles.append({
                            "time": candle.time, "open": self._cast_money(candle.open),
                            "high": self._cast_money(candle.high), "low": self._cast_money(candle.low),
                            "close": self._cast_money(candle.close), "volume": candle.volume,
                        })
                if pbar.n < days:
                    pbar.update(days - pbar.n)

        except RequestError as e:
            # Ловим специфичные ошибки API и выводим понятные сообщения
            logging.error(f"Ошибка API при получении данных для {figi}: {e.details} (код: {e.code.name})")
            if e.code == StatusCode.NOT_FOUND:  # Код 50002
                logging.error(f"-> Инструмент не найден. Проверьте правильность FIGI: {figi}")
            elif e.code == StatusCode.RESOURCE_EXHAUSTED:  # Код 80002
                # Примечание: RESOURCE_EXHAUSTED может быть и по другим причинам,
                # но в контексте загрузки данных это почти всегда лимит запросов.
                logging.error("-> Превышен лимит запросов к API. Попробуйте позже или уменьшите период.")
            elif e.code == StatusCode.INVALID_ARGUMENT:  # Коды 3xxxx
                # Ошибка 30084 (превышен период) попадает сюда.
                # Можно проверить детали, если нужно.
                if "Maximum request period has been exceeded" in e.details:
                    logging.error(f"-> Превышен максимальный период запроса для интервала '{interval_str}'.")
                else:
                    logging.error(f"-> Неверный аргумент в запросе: {e.details}")

            return pd.DataFrame()
        except Exception as e:
            # Ловим любые другие непредвиденные ошибки
            logging.error(f"Непредвиденная ошибка при загрузке {figi}: {e}")
            return pd.DataFrame()

        # Превращаем список словарей в DataFrame
        df = pd.DataFrame(all_candles)

        # Проверка на пустой результат
        if df.empty:
            logging.warning(f"Для {figi} не было возвращено ни одной свечи.")
            logging.warning(
                "-> Возможные причины: неторговый период, неверный FIGI или ограничения API по глубине истории.")
        # Проверка на неполные данные
        else:
            df['time'] = pd.to_datetime(df['time'])
            # Проверяем, сколько дней данных мы ФАКТИЧЕСКИ получили
            actual_start_date = df['time'].iloc[0].date()
            actual_days_loaded = (df['time'].iloc[-1].date() - actual_start_date).days + 1

            # Сравниваем запрошенное с полученным
            if actual_days_loaded < days - 14:  # Даем погрешность в 2 недели на выходные и праздники
                logging.warning(
                    f"Запрошено {days} дней, но фактически загружено только ~{actual_days_loaded} дней, начиная с {actual_start_date}.")
                logging.warning("-> Это может быть связано с ограничениями API по глубине истории для интервала "
                                f"'{interval_str}'.")

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