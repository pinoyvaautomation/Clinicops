from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones


FRIENDLY_TIMEZONE_NAMES = {
    'UTC': 'Coordinated Universal Time',
    'Asia/Manila': 'Philippines',
    'Asia/Singapore': 'Singapore',
    'Asia/Kuala_Lumpur': 'Malaysia',
    'Asia/Hong_Kong': 'Hong Kong',
    'Asia/Jakarta': 'Indonesia (Western)',
    'Asia/Tokyo': 'Japan',
    'Asia/Seoul': 'South Korea',
    'Asia/Dubai': 'United Arab Emirates',
    'Asia/Kolkata': 'India',
    'Australia/Sydney': 'Australia (Sydney)',
    'Europe/London': 'United Kingdom',
    'Europe/Paris': 'Central Europe',
    'America/New_York': 'US Eastern',
    'America/Chicago': 'US Central',
    'America/Denver': 'US Mountain',
    'America/Los_Angeles': 'US Pacific',
    'America/Toronto': 'Canada Eastern',
    'Pacific/Auckland': 'New Zealand',
}

PRIORITY_TIMEZONES = [
    'Asia/Manila',
    'Asia/Singapore',
    'Asia/Kuala_Lumpur',
    'Asia/Hong_Kong',
    'Asia/Tokyo',
    'Asia/Seoul',
    'Australia/Sydney',
    'UTC',
    'Europe/London',
    'Europe/Paris',
    'America/New_York',
    'America/Chicago',
    'America/Denver',
    'America/Los_Angeles',
]


def _is_supported_timezone(value: str) -> bool:
    if value in {'Factory', 'localtime'}:
        return False
    return not value.startswith(('posix/', 'right/'))


def _friendly_location(value: str) -> str:
    mapped = FRIENDLY_TIMEZONE_NAMES.get(value)
    if mapped:
        return mapped

    parts = [part.replace('_', ' ') for part in value.split('/')]
    if len(parts) == 1:
        return parts[0]
    return f'{parts[-1]} ({parts[0]})'


def _offset_label(value: str) -> str:
    current = datetime.now(ZoneInfo(value))
    offset = current.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = '+' if total_minutes >= 0 else '-'
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f'UTC{sign}{hours:02d}:{minutes:02d}'


@lru_cache(maxsize=1)
def get_timezone_choices() -> tuple[tuple[str, str], ...]:
    supported = [tz for tz in available_timezones() if _is_supported_timezone(tz)]
    seen = set()
    ordered = []

    for tz in PRIORITY_TIMEZONES:
        if tz in supported and tz not in seen:
            ordered.append(tz)
            seen.add(tz)

    remaining = sorted((tz for tz in supported if tz not in seen), key=lambda tz: timezone_display_label(tz))
    ordered.extend(remaining)

    return tuple((tz, timezone_display_label(tz)) for tz in ordered)


def timezone_display_label(value: str | None) -> str:
    if not value:
        value = 'UTC'
    try:
        ZoneInfo(value)
    except Exception:
        return value
    return f'{_offset_label(value)} • {_friendly_location(value)} ({value})'
