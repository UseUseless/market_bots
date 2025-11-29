"""
Модуль вспомогательных функций времени.

Содержит утилиты для конвертации строковых представлений интервалов
в объекты времени Python и управления часовыми поясами.
"""

from datetime import timedelta, timezone


def parse_interval_to_timedelta(interval_str: str) -> timedelta:
    """
    Преобразует строку интервала (из конфига или API) в объект `timedelta`.

    Поддерживает форматы: 'Xmin', 'Xhour', 'Xday', 'Xweek', 'Xmonth'.
    Для месяца используется упрощение: 1 месяц = 30 дней.

    Args:
        interval_str (str): Строка интервала, например '5min', '4hour', '1week'.

    Returns:
        timedelta: Объект разницы во времени.
                   Возвращает `timedelta(0)`, если формат не распознан.
    """
    if not interval_str:
        return timedelta(0)

    s = interval_str.lower().strip()

    try:
        # Парсинг минут
        if s.endswith("min"):
            value = int(s.replace("min", ""))
            return timedelta(minutes=value)

        # Парсинг часов
        elif s.endswith("hour"):
            value = int(s.replace("hour", ""))
            return timedelta(hours=value)

        # Парсинг дней
        elif s.endswith("day"):
            value = int(s.replace("day", ""))
            return timedelta(days=value)

        # Парсинг недель
        elif s.endswith("week"):
            value = int(s.replace("week", ""))
            return timedelta(weeks=value)

        # Парсинг месяцев (аппроксимация)
        elif s.endswith("month"):
            value = int(s.replace("month", ""))
            # Timedelta не поддерживает месяцы, берем среднее
            return timedelta(days=value * 30)

    except ValueError:
        # Если не удалось преобразовать числовую часть (например, 'min' без числа)
        pass

    # Дефолтное значение для неизвестных форматов (например, тикер без интервала)
    return timedelta(0)


def msk_timezone() -> timezone:
    """
    Возвращает объект часового пояса UTC+3 (Москва).

    Используется для форматирования времени в логах и уведомлениях,
    чтобы пользователю было удобнее читать время событий.

    Returns:
        timezone: Объект таймзоны с смещением +3 часа.
    """
    return timezone(timedelta(hours=3))