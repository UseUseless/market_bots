from datetime import timedelta, timezone

def parse_interval_to_timedelta(interval_str: str) -> timedelta:
    """
    Преобразует строковый интервал (например, '5min', '4hour') в объект timedelta.
    """
    try:
        if "min" in interval_str:
            # "15min" -> 15
            minutes = int(interval_str.replace("min", ""))
            return timedelta(minutes=minutes)
        elif "hour" in interval_str:
            hours = int(interval_str.replace("hour", ""))
            return timedelta(hours=hours)
        elif "day" in interval_str:
            if interval_str == "1day": return timedelta(days=1)
            days = int(interval_str.replace("day", ""))
            return timedelta(days=days)
        elif "week" in interval_str:
            return timedelta(weeks=1)
    except ValueError:
        pass

    # Дефолт, если не смогли распарсить (например, для тикеров без интервала)
    return timedelta(minutes=0)


def msk_timezone():
    """Возвращает таймзону UTC+3 (МСК)."""
    return timezone(timedelta(hours=3))