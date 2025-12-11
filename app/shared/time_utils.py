"""
Модуль вспомогательных функций времени.

Содержит утилиты для конвертации интервалов и управления часовыми поясами.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from functools import lru_cache
import logging

from app.shared.config import config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def interval_to_timedelta(interval_str: str) -> timedelta:
    """
    Преобразует строку интервала (из конфига или API) в объект `timedelta`.

    Args:
        interval_str (str): Строка интервала, например '5min', '4hour'.

    Returns:
        timedelta: Объект разницы во времени.
    """
    if not interval_str:
        return timedelta(0)

    s = interval_str.lower().strip()

    try:
        if s.endswith("min"):
            return timedelta(minutes=int(s.replace("min", "")))

        elif s.endswith("hour"):
            return timedelta(hours=int(s.replace("hour", "")))

        elif s.endswith("day"):
            return timedelta(days=int(s.replace("day", "")))

        elif s.endswith("week"):
            return timedelta(weeks=int(s.replace("week", "")))

        elif s.endswith("month"):
            # Упрощение: 1 месяц = 30 дней
            return timedelta(days=int(s.replace("month", "")) * 30)

    except ValueError:
        pass

    return timedelta(0)


def get_display_timezone() -> ZoneInfo:
    """
    Возвращает объект часового пояса из настроек.
    Используется для форматирования уведомлений пользователю.

    Если зона в конфиге указана неверно, откатывается к UTC.
    """
    tz_name = config.DISPLAY_TIMEZONE
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.error(f"Timezone '{tz_name}' not found in system. Falling back to UTC.")
        return ZoneInfo("UTC")