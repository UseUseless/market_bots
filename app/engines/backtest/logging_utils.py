import logging
import os

from app.core.logging_setup import backtest_time_filter


def setup_backtest_logging(log_file_path: str):
    """
    Настраивает логирование специально для сессии бэктеста.
    Создает файл для логов этого конкретного запуска.
    """
    # Форматтер, использующий 'sim_time' из нашего кастомного фильтра
    log_formatter = logging.Formatter('%(sim_time)s - %(levelname)s - %(name)s - %(message)s')

    # Убеждаемся, что директория для логов существует
    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)

    # Файловый обработчик (пишет в файл)
    file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    file_handler.setFormatter(log_formatter)

    # Консольный обработчик (выводит в терминал)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    # Настраиваем наш корневой логгер 'backtester'
    app_logger = logging.getLogger('backtester')
    app_logger.setLevel(logging.INFO)

    # Очищаем предыдущие обработчики, если они были
    if app_logger.hasHandlers():
        app_logger.handlers.clear()

    # Добавляем новые обработчики и фильтр
    app_logger.addHandler(file_handler)
    app_logger.addHandler(console_handler)
    app_logger.addFilter(backtest_time_filter)

    # Отключаем распространение логов выше, чтобы избежать дублирования
    app_logger.propagate = False

    # Уменьшаем "шум" от сторонних библиотек
    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)
