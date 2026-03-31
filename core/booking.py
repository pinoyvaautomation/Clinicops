from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .models import Appointment, Staff


@dataclass(frozen=True)
class Slot:
    staff: Staff
    start_at: datetime
    end_at: datetime

    @property
    def value(self) -> str:
        return f'{self.staff.id}|{int(self.start_at.timestamp())}'

    @property
    def label(self) -> str:
        return f'{self.start_at:%b %d, %Y %I:%M %p}'


def build_available_slots(
    clinic,
    staff_list,
    duration_minutes: int | None = None,
    now: datetime | None = None,
    exclude_appointment_id: int | None = None,
) -> list[Slot]:
    tz = ZoneInfo(clinic.timezone or 'UTC')
    slot_minutes = duration_minutes or getattr(settings, 'APPOINTMENT_SLOT_MINUTES', 30)
    day_start = getattr(settings, 'APPOINTMENT_DAY_START', 9)
    day_end = getattr(settings, 'APPOINTMENT_DAY_END', 17)
    days_ahead = getattr(settings, 'APPOINTMENT_DAYS_AHEAD', 7)

    now_local = timezone.localtime(now or timezone.now(), tz)
    start_date = now_local.date()
    end_date = start_date + timedelta(days=days_ahead)

    range_start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    range_end = timezone.make_aware(datetime.combine(end_date, time.max), tz)

    appointments = (
        Appointment.objects.filter(
            clinic=clinic,
            status=Appointment.Status.SCHEDULED,
            start_at__gte=range_start,
            start_at__lte=range_end,
        )
        .exclude(pk=exclude_appointment_id)
        .order_by('start_at')
        .only('id', 'staff_id', 'start_at', 'end_at')
    )

    intervals_by_staff: dict[int, list[tuple[datetime, datetime]]] = {}
    for appt in appointments:
        intervals_by_staff.setdefault(appt.staff_id, []).append((appt.start_at, appt.end_at))

    slots: list[Slot] = []
    for staff in staff_list:
        intervals = intervals_by_staff.get(staff.id, [])
        for day_offset in range(days_ahead + 1):
            day = start_date + timedelta(days=day_offset)
            day_start_dt = timezone.make_aware(datetime.combine(day, time(hour=day_start)), tz)
            day_end_dt = timezone.make_aware(datetime.combine(day, time(hour=day_end)), tz)

            current = day_start_dt
            while current + timedelta(minutes=slot_minutes) <= day_end_dt:
                slot_start = current
                slot_end = current + timedelta(minutes=slot_minutes)

                if slot_start <= now_local:
                    current += timedelta(minutes=slot_minutes)
                    continue

                overlaps = any(
                    existing_start < slot_end and existing_end > slot_start
                    for existing_start, existing_end in intervals
                )
                if not overlaps:
                    slots.append(Slot(staff=staff, start_at=slot_start, end_at=slot_end))

                current += timedelta(minutes=slot_minutes)

    slots.sort(key=lambda slot: slot.start_at)
    return slots


def parse_slot_value(value: str) -> tuple[int, datetime]:
    staff_id_str, start_str = value.split('|', 1)
    staff_id = int(staff_id_str)
    start_at = datetime.fromtimestamp(int(start_str), tz=dt_timezone.utc)
    return staff_id, start_at
