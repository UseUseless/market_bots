"""
Модуль настройки логирования.

Отвечает за конфигурацию вывода логов в консоль и файлы.
Реализует специфические механизмы для разных режимов работы приложения:
1.  **CLI/Optimization**: Интеграция с `tqdm` (прогресс-барами), чтобы логи не ломали визуализацию.
2.  **Backtest**: Внедрение "времени симуляции" в логи, чтобы при анализе
    было понятно, в какой исторический момент произошло событие.
3.  **Global**: Подавление шума от сторонних библиотек (Matplotlib, Optuna).
"""

import logging
import os
import sys
from datetime import datetime
from threading import local
from tqdm import tqdm
import optuna


class TqdmLoggingHandler(logging.Handler):
    """
    Кастомный обработчик логов для корректной работы с прогресс-барами `tqdm`.

    Если использовать стандартный `StreamHandler`, вывод логов будет "разрывать"
    строку прогресс-бара. Этот класс использует `tqdm.write()`, который аккуратно
    печатает сообщение *над* прогресс-баром.
    """

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        """
        Перехватывает запись лога и выводит её через tqdm.
        """
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)


def setup_global_logging(mode: str = 'default', log_level: int = logging.INFO):
    """
    Централизованная настройка корневого логгера приложения.

    Очищает существующие хендлеры и устанавливает новые в зависимости от режима.
    Также настраивает уровень шума для сторонних библиотек.

    Args:
        mode (str): Режим вывода.
            - 'default': Стандартный вывод в stdout (для обычного запуска).
            - 'tqdm': Вывод через TqdmLoggingHandler (для скриптов с прогресс-барами).
        log_level (int): Уровень логирования (DEBUG, INFO, WARNING...).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Удаляем старые хендлеры, чтобы избежать дублирования логов
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    if mode == 'tqdm':
        # Краткий формат для CLI (только уровень и сообщение)
        formatter = logging.Formatter('%(levelname)s - %(message)s')
        handler = TqdmLoggingHandler()
    else:
        # Полный формат с временем для обычного режима
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # --- Настройка сторонних библиотек ---
    # Отключаем лишний шум, оставляя только важные предупреждения
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)

    # Optuna очень болтлива, отключаем её внутренний логгер и прокидывание наверх
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    optuna_logger = logging.getLogger('optuna')
    optuna_logger.handlers.clear()
    optuna_logger.propagate = False


class BacktestTimeFilter(logging.Filter):
    """
    Фильтр логирования для внедрения времени симуляции.

    В режиме бэктеста реальное время (wall-clock time) не имеет значения.
    Важно знать, какое время было "внутри" симуляции, когда произошло событие.

    Использует `threading.local`, чтобы потокобезопасно хранить время
    для каждого запущенного бэктеста (если они идут параллельно).
    """

    def __init__(self):
        super().__init__()
        self._storage = local()
        self._storage.sim_time = None

    def set_sim_time(self, dt: datetime):
        """
        Устанавливает текущее время симуляции для текущего потока.
        Вызывается движком бэктеста на каждой новой свече.

        Args:
            dt (datetime): Текущее время в симуляции.
        """
        self._storage.sim_time = dt.strftime('%Y-%m-%d %H:%M:%S')

    def reset_sim_time(self):
        """Сбрасывает время симуляции (например, при завершении теста)."""
        self._storage.sim_time = None

    def filter(self, record):
        """
        Добавляет атрибут `sim_time` к каждой записи лога.

        Если время не установлено (этап инициализации), используется метка 'SETUP'.
        В формате логов можно использовать `%(sim_time)s`.
        """
        record.sim_time = getattr(self._storage, 'sim_time', None) or "SETUP"
        return True


# Глобальный экземпляр фильтра, который импортируют другие модули
backtest_time_filter = BacktestTimeFilter()


def setup_backtest_logging(log_file_path: str):
    """
    Настраивает специфичный логгер для одиночного прогона бэктеста.

    В отличие от глобального логгера, этот пишет в файл и использует
    формат с временем симуляции.

    Args:
        log_file_path (str): Полный путь к файлу лога.
    """
    # Форматтер использует кастомное поле %(sim_time)s из BacktestTimeFilter
    log_formatter = logging.Formatter('%(sim_time)s - %(levelname)s - %(name)s - %(message)s')

    # Гарантируем существование папки
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

    # Файловый хендлер (перезаписывает файл при каждом запуске mode='w')
    file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    file_handler.setFormatter(log_formatter)

    # Консольный хендлер (чтобы видеть прогресс в терминале)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    # Настройка логгера 'backtester'
    app_logger = logging.getLogger('backtester')
    app_logger.setLevel(logging.INFO)

    # Очистка старых хендлеров (критично при повторных запусках из кода)
    if app_logger.hasHandlers():
        app_logger.handlers.clear()

    # Подключение хендлеров и фильтра времени
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    app_logger.addFilter(backtest_time_filter)

    # Отключаем всплытие (propagate), чтобы логи не дублировались в корневом логгере
    app_logger.propagate = False

    # Повторно глушим шумные библиотеки, на случай если они проснулись
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)