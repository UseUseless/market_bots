# --- ОБЯЗАТЕЛЬНЫЕ ИМПОРТЫ ---
# Стандартные и сторонние библиотеки
from queue import Queue
import logging
import pandas as pd
import pandas_ta as ta  # Библиотека для технических индикаторов

# Компоненты вашего фреймворка
from core.event import MarketEvent, SignalEvent
from strategies.base_strategy import BaseStrategy
# -----------------------------


# --- 1. ОПРЕДЕЛЕНИЕ КЛАССА ---
# Название класса должно быть уникальным и отражать суть стратегии.
# Он ОБЯЗАТЕЛЬНО должен наследоваться от BaseStrategy.
class StrategyTemplate(BaseStrategy):
    """
    Шаблон для создания новой торговой стратегии.
    Скопируйте этот файл, переименуйте класс (здесь и в run.py) и измените логику.
    """

    # --- 2. КОНСТРУКТОР: "ПАСПОРТ" СТРАТЕГИИ ---
    def __init__(self, events_queue: Queue, figi: str):
        # Эта строка обязательна. Она инициализирует базовый класс.
        super().__init__(events_queue, figi)

        # --- ПАРАМЕТРЫ СТРАТЕГИИ (ОБЯЗАТЕЛЬНО ИЗМЕНИТЬ) ---
        # Уникальное имя для логов и отчетов
        self.name = "StrategyTemplate"
        
        # Таймфрейм, на котором будет работать стратегия
        self.candle_interval = "5min"  # Варианты: "1min", "5min", "15min", "1hour", "1day"
        
        # Параметры управления риском
        self.stop_loss_percent = 1.5   # Например, 1.5%
        self.take_profit_percent = 3.0 # Например, 3.0%
        # ----------------------------------------------------

        # --- Внутренние переменные для работы стратегии (можно добавлять свои) ---
        # Длина истории, необходимая для расчета самого "длинного" индикатора
        self.required_history_len = 50 
        
        # Контейнер для хранения последних N свечей
        self.data_history = []


    # --- 3. ПОДГОТОВКА ДАННЫХ: РАСЧЕТ ИНДИКАТОРОВ ---
    def prepare_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Этот метод вызывается один раз перед началом бэктеста.
        Ваша задача - добавить в DataFrame 'data' все нужные вам индикаторы.
        """
        logging.info(f"Стратегия '{self.name}' рассчитывает свои индикаторы...")
        
        # --- ВАША ЛОГИКА РАСЧЕТА ИНДИКАТОРОВ (ИЗМЕНИТЬ ПОД ВАШУ СТРАТЕГИЮ) ---
        # Пример: Расчет простой скользящей средней (SMA)
        data.ta.sma(length=self.required_history_len, append=True, col_names=(f'SMA_{self.required_history_len}',))
        
        # Пример: Расчет RSI
        data.ta.rsi(length=14, append=True, col_names=('RSI_14',))
        # ----------------------------------------------------------------------
        
        # Эта часть обязательна. Она удаляет строки с пустыми значениями (NaN),
        # которые образуются в начале после расчета индикаторов.
        data.dropna(inplace=True)
        data.reset_index(drop=True, inplace=True)
        
        logging.info("Подготовка данных для стратегии завершена.")
        return data


    # --- 4. ЛОГИКА СИГНАЛОВ: ПРИНЯТИЕ РЕШЕНИЙ ---
    def calculate_signals(self, event: MarketEvent):
        """
        Этот метод вызывается для КАЖДОЙ свечи в истории.
        Здесь вы анализируете данные и решаете, нужно ли генерировать сигнал.
        """
        # Накапливаем историю свечей до нужной длины
        self.data_history.append(event.data)
        if len(self.data_history) > self.required_history_len:
            self.data_history.pop(0)
        if len(self.data_history) < self.required_history_len:
            return  # Недостаточно данных для анализа, выходим

        # Получаем последнюю свечу (она содержит и цены, и уже рассчитанные индикаторы)
        last_candle = self.data_history[-1]

        # --- ВАША ТОРГОВАЯ ЛОГИКА (ИЗМЕНИТЬ ПОЛНОСТЬЮ) ---
        # Пример простой логики:
        # Покупаем, если цена закрытия пересекла SMA снизу вверх.
        # Продаем (закрываем позицию), если цена закрытия ушла ниже SMA.
        
        sma_value = last_candle[f'SMA_{self.required_history_len}']
        
        # Условие на ПОКУПКУ (BUY)
        if last_candle['close'] > sma_value:
            # Если условия выполнены, создаем SignalEvent и кладем его в очередь.
            # Фреймворк сам решит, нужно ли открывать позицию.
            signal = SignalEvent(figi=self.figi, direction="BUY", strategy_id=self.name)
            self.events_queue.put(signal)

        # Условие на ПРОДАЖУ (SELL)
        elif last_candle['close'] < sma_value:
            # Генерируем сигнал на продажу. Фреймворк сам решит, нужно ли закрывать позицию.
            signal = SignalEvent(figi=self.figi, direction="SELL", strategy_id=self.name)
            self.events_queue.put(signal)

'''
Как с этим работать

Копируете файл strategy_template.py и переименовываете его, например, в ma_cross_strategy.py.
Переименовываете класс StrategyTemplate в MaCrossStrategy.
Редактируете "паспорт" в __init__: меняете name, candle_interval, stop_loss_percent и т.д.
Редактируете prepare_data: добавляете расчет нужных вам индикаторов (например, две скользящие средние).
Полностью переписываете логику в calculate_signals под вашу новую идею (например, покупка при пересечении быстрой SMA медленной снизу вверх).
Регистрируете новый класс MaCrossStrategy в run.py.
Запускаете бэктест.
'''