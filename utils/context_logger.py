import logging
from datetime import datetime

class BacktestTimeFilter(logging.Filter):
    """
    Фильтр, который добавляет в запись лога время симуляции ('sim_time').
    Предназначен ИСКЛЮЧИТЕЛЬНО для режима бэктестинга.
    """
    def __init__(self):
        super().__init__()
        from threading import local
        self._storage = local()
        self._storage.sim_time = None

    def set_sim_time(self, dt: datetime):
        """
        Устанавливает текущее время симуляции.
        Ожидается, что dt всегда будет объектом datetime.
        """
        self._storage.sim_time = dt.strftime('%Y-%m-%d %H:%M:%S')

    def reset_sim_time(self):
        """Сбрасывает время симуляции в конце бэктеста."""
        self._storage.sim_time = None

    def filter(self, record):
        """
        Добавляет 'sim_time' к каждой записи лога.
        Если время симуляции еще не установлено (например, на этапе инициализации),
        использует заглушку 'SETUP'.
        """
        record.sim_time = self._storage.sim_time or "SETUP"
        return True

# --- Глобальный экземпляр фильтра с новым, говорящим названием ---
backtest_time_filter = BacktestTimeFilter()