from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import Appointment


def send_upcoming_appointment_reminders() -> int:
    now = timezone.now()
    window_minutes = getattr(settings, 'REMINDER_WINDOW_MINUTES', 1440)
    window_end = now + timedelta(minutes=window_minutes)

    upcoming = (
        Appointment.objects.filter(
            status=Appointment.Status.SCHEDULED,
            start_at__gte=now,
            start_at__lte=window_end,
            reminder_sent_at__isnull=True,
        )
        .select_related('clinic', 'staff', 'patient', 'appointment_type')
        .order_by('start_at')
    )

    sent_count = 0
    for appt in upcoming:
        if not appt.patient.email:
            continue

        tz = ZoneInfo(appt.clinic.timezone or 'UTC')
        start_local = timezone.localtime(appt.start_at, tz)
        end_local = timezone.localtime(appt.end_at, tz)

        subject = f'Appointment reminder - {appt.clinic.name}'
        appointment_type_line = ''
        if appt.appointment_type:
            appointment_type_line = f'Type: {appt.appointment_type.name}\n'
        body = (
            f'Hello {appt.patient.first_name},\n\n'
            f'This is a reminder for your appointment at {appt.clinic.name}.\n'
            f'Staff: {appt.staff}\n'
            f'{appointment_type_line}'
            f'Time: {start_local:%b %d, %Y %I:%M %p} - {end_local:%I:%M %p} ({appt.clinic.timezone})\n\n'
            f'Confirmation code: {appt.confirmation_code}\n\n'
            'If you need to reschedule, please contact the clinic.\n'
        )

        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [appt.patient.email],
            fail_silently=False,
        )

        appt.reminder_sent_at = now
        appt.save(update_fields=['reminder_sent_at'])
        sent_count += 1

    return sent_count
