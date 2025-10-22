import logging
from datetime import datetime

class ContextFilter(logging.Filter):
    """
    Фильтр, который добавляет в запись лога кастомный атрибут 'sim_time'.
    """
    def __init__(self):
        super().__init__()
        # Используем Thread-Local Storage для хранения времени симуляции.
        # Это гарантирует, что время будет корректным даже в многопоточных приложениях.
        from threading import local
        self._storage = local()
        self._storage.sim_time = None

    def set_sim_time(self, dt: datetime):
        """Устанавливает текущее время симуляции."""
        if dt:
            self._storage.sim_time = dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            self._storage.sim_time = None

    def filter(self, record):
        """Добавляет 'sim_time' к каждой записи лога."""
        record.sim_time = self._storage.sim_time or datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        return True

# --- Глобальный экземпляр фильтра, который мы будем использовать ---
context_filter = ContextFilter()