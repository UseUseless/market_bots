import logging
import sys
from tqdm import tqdm
import optuna

class TqdmLoggingHandler(logging.Handler):
    """
    Перенаправляет вывод логов через tqdm.write(), чтобы не ломать
    отображение прогресс-баров. Используется в runner.py.
    """

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)

def setup_global_logging(mode: str = 'default', log_level: int = logging.INFO):
    """
    Централизованно настраивает логирование для всего приложения.

    :param mode: 'default' для стандартного вывода, 'tqdm' для интеграции с tqdm.
    :param log_level: Уровень логирования для корневого логгера.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    if mode == 'tqdm':
        formatter = logging.Formatter('%(levelname)s - %(message)s')
        handler = TqdmLoggingHandler()
    else:
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    logging.getLogger('matplotlib').setLevel(logging.WARNING)
    logging.getLogger('PIL').setLevel(logging.WARNING)

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    optuna_logger = logging.getLogger('optuna')
    optuna_logger.handlers.clear()
    optuna_logger.propagate = False