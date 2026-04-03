import logging
from datetime import datetime
from typing import Iterable

from django.urls import reverse
from django.utils import timezone
from zoneinfo import ZoneInfo

from .models import (
    Appointment,
    Clinic,
    ClinicMessagingPermission,
    Message,
    MessageThread,
    MessageThreadReadState,
    Patient,
    Staff,
)

logger = logging.getLogger(__name__)

ROLE_SEQUENCE = [
    ClinicMessagingPermission.Role.ADMIN,
    ClinicMessagingPermission.Role.DOCTOR,
    ClinicMessagingPermission.Role.NURSE,
    ClinicMessagingPermission.Role.FRONT_DESK,
]

DEFAULT_ROLE_ACCESS = {
    ClinicMessagingPermission.Role.ADMIN: ClinicMessagingPermission.AccessLevel.REPLY,
    ClinicMessagingPermission.Role.DOCTOR: ClinicMessagingPermission.AccessLevel.REPLY,
    ClinicMessagingPermission.Role.NURSE: ClinicMessagingPermission.AccessLevel.VIEW_ONLY,
    ClinicMessagingPermission.Role.FRONT_DESK: ClinicMessagingPermission.AccessLevel.REPLY,
}


def ensure_clinic_messaging_permissions(clinic: Clinic) -> None:
    # Messaging defaults are clinic-scoped and only four rows per clinic, so lazy create is cheap.
    for role in ROLE_SEQUENCE:
        ClinicMessagingPermission.objects.get_or_create(
            clinic=clinic,
            role=role,
            defaults={'access_level': DEFAULT_ROLE_ACCESS[role]},
        )


def clinic_owner_user(clinic: Clinic):
    if clinic.owner_user_id:
        return clinic.owner_user
    owner_staff = (
        Staff.objects.filter(clinic=clinic, user__groups__name='Admin')
        .select_related('user')
        .order_by('id')
        .first()
    )
    return owner_staff.user if owner_staff else None


def user_is_clinic_owner(user, clinic: Clinic) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if clinic.owner_user_id:
        return clinic.owner_user_id == user.id
    owner_user = clinic_owner_user(clinic)
    return bool(owner_user and owner_user.pk == user.pk)


def user_staff_role(user) -> str:
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    return (
        user.groups.filter(name__in=ROLE_SEQUENCE)
        .values_list('name', flat=True)
        .first()
        or ''
    )


def clinic_messaging_access_map(clinic: Clinic) -> dict[str, str]:
    ensure_clinic_messaging_permissions(clinic)
    return {
        row.role: row.access_level
        for row in ClinicMessagingPermission.objects.filter(clinic=clinic)
    }


def user_can_manage_messaging_settings(user, clinic: Clinic) -> bool:
    return user_is_clinic_owner(user, clinic)


def messaging_access_level_for_user(user, clinic: Clinic) -> str:
    if user_can_manage_messaging_settings(user, clinic):
        return ClinicMessagingPermission.AccessLevel.REPLY
    role = user_staff_role(user)
    if not role:
        return ClinicMessagingPermission.AccessLevel.NONE
    return clinic_messaging_access_map(clinic).get(role, ClinicMessagingPermission.AccessLevel.NONE)


def user_can_view_messages(user, clinic: Clinic) -> bool:
    return messaging_access_level_for_user(user, clinic) in {
        ClinicMessagingPermission.AccessLevel.VIEW_ONLY,
        ClinicMessagingPermission.AccessLevel.REPLY,
    }


def user_can_reply_messages(user, clinic: Clinic) -> bool:
    return messaging_access_level_for_user(user, clinic) == ClinicMessagingPermission.AccessLevel.REPLY


def messaging_role_rows(clinic: Clinic) -> list[dict]:
    access_map = clinic_messaging_access_map(clinic)
    return [
        {
            'role': role,
            'label': ClinicMessagingPermission.Role(role).label,
            'access_level': access_map.get(role, DEFAULT_ROLE_ACCESS[role]),
        }
        for role in ROLE_SEQUENCE
    ]


def thread_subject_for_appointment(appointment: Appointment) -> str:
    if appointment.appointment_type_id:
        return f'{appointment.appointment_type.name} · {appointment.confirmation_code}'
    return f'Appointment · {appointment.confirmation_code}'


def get_or_create_appointment_thread(appointment: Appointment) -> MessageThread:
    thread, created = MessageThread.objects.get_or_create(
        appointment=appointment,
        defaults={
            'clinic': appointment.clinic,
            'patient': appointment.patient,
            'subject': thread_subject_for_appointment(appointment),
            'source': MessageThread.Source.APPOINTMENT,
            'last_message_sender_type': MessageThread.SenderType.PATIENT,
            'last_message_excerpt': '',
        },
    )
    if not created:
        updated = False
        if thread.clinic_id != appointment.clinic_id:
            thread.clinic = appointment.clinic
            updated = True
        if thread.patient_id != appointment.patient_id:
            thread.patient = appointment.patient
            updated = True
        if not thread.subject:
            thread.subject = thread_subject_for_appointment(appointment)
            updated = True
        if thread.source != MessageThread.Source.APPOINTMENT:
            thread.source = MessageThread.Source.APPOINTMENT
            updated = True
        if updated:
            thread.save(update_fields=['clinic', 'patient', 'subject', 'source'])
    return thread


def create_patient_portal_thread(*, patient: Patient, subject: str, body: str, sender_user=None) -> MessageThread:
    thread = MessageThread.objects.create(
        clinic=patient.clinic,
        patient=patient,
        subject=(subject or '').strip()[:140],
        source=MessageThread.Source.PORTAL,
        last_message_sender_type=MessageThread.SenderType.PATIENT,
    )
    append_message(
        thread,
        sender_type=Message.SenderType.PATIENT,
        sender_user=sender_user,
        sender_label=patient_display_name(patient),
        body=body,
    )
    return thread


def patient_display_name(patient: Patient) -> str:
    return f'{patient.first_name} {patient.last_name}'.strip()


def staff_display_name(user) -> str:
    return user.get_full_name().strip() or user.email or user.username


def message_excerpt(body: str, limit: int = 180) -> str:
    clean_body = ' '.join((body or '').split())
    if len(clean_body) <= limit:
        return clean_body
    return clean_body[: limit - 3].rstrip() + '...'


def append_message(
    thread: MessageThread,
    *,
    sender_type: str,
    body: str,
    sender_user=None,
    sender_label: str = '',
) -> Message:
    message = Message.objects.create(
        thread=thread,
        sender_user=sender_user,
        sender_type=sender_type,
        sender_label=(sender_label or '').strip()[:150],
        body=body.strip(),
    )
    thread.last_message_sender_type = sender_type
    thread.last_message_excerpt = message_excerpt(body)
    thread.last_message_at = message.created_at
    if thread.status != MessageThread.Status.OPEN:
        thread.status = MessageThread.Status.OPEN
        thread.save(update_fields=['last_message_sender_type', 'last_message_excerpt', 'last_message_at', 'status', 'updated_at'])
    else:
        thread.save(update_fields=['last_message_sender_type', 'last_message_excerpt', 'last_message_at', 'updated_at'])
    return message


def mark_thread_read(thread: MessageThread, user, *, when=None) -> None:
    if not user or not getattr(user, 'is_authenticated', False):
        return
    MessageThreadReadState.objects.update_or_create(
        thread=thread,
        user=user,
        defaults={'last_read_at': when or timezone.now()},
    )


def thread_is_unread_for_user(thread: MessageThread, user, read_map: dict[int, datetime] | None = None) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    is_staff_user = hasattr(user, 'staff')
    if is_staff_user and thread.last_message_sender_type != MessageThread.SenderType.PATIENT:
        return False
    if not is_staff_user and thread.last_message_sender_type != MessageThread.SenderType.STAFF:
        return False
    last_read_at = (read_map or {}).get(thread.id)
    return last_read_at is None or last_read_at < thread.last_message_at


def read_map_for_user(user, thread_ids: Iterable[int]) -> dict[int, datetime]:
    thread_ids = list(thread_ids)
    if not user or not getattr(user, 'is_authenticated', False) or not thread_ids:
        return {}
    return {
        thread_id: last_read_at
        for thread_id, last_read_at in MessageThreadReadState.objects.filter(
            user=user,
            thread_id__in=thread_ids,
        ).values_list('thread_id', 'last_read_at')
    }


def message_threads_for_staff(clinic: Clinic):
    return (
        MessageThread.objects.filter(clinic=clinic)
        .select_related('patient', 'appointment__appointment_type', 'appointment', 'clinic')
        .order_by('-last_message_at', '-created_at')
    )


def message_threads_for_patient(patient: Patient):
    return (
        MessageThread.objects.filter(patient=patient)
        .select_related('appointment__appointment_type', 'appointment', 'clinic', 'patient')
        .order_by('-last_message_at', '-created_at')
    )


def thread_title_for_staff(thread: MessageThread) -> str:
    patient_name = patient_display_name(thread.patient)
    if thread.subject:
        return f'{patient_name} · {thread.subject}'
    return patient_name


def thread_title_for_patient(thread: MessageThread) -> str:
    if thread.subject:
        return thread.subject
    if thread.appointment_id:
        return thread_subject_for_appointment(thread.appointment)
    return f'{thread.clinic.name} conversation'


def thread_meta_for_staff(thread: MessageThread) -> str:
    if thread.appointment_id and thread.appointment.start_at:
        local_start = timezone.localtime(thread.appointment.start_at, ZoneInfo(thread.clinic.timezone or 'UTC'))
        return local_start.strftime('%b %d · %I:%M %p')
    return thread.patient.email


def thread_meta_for_patient(thread: MessageThread) -> str:
    if thread.appointment_id and thread.appointment.start_at:
        local_start = timezone.localtime(thread.appointment.start_at, ZoneInfo(thread.clinic.timezone or 'UTC'))
        return local_start.strftime('%b %d · %I:%M %p')
    return thread.clinic.name


def build_thread_preview_rows(*, user, threads, limit: int, title_fn, meta_fn):
    thread_ids = [thread.id for thread in threads]
    state_map = read_map_for_user(user, thread_ids)
    unread_count = 0
    preview_rows = []
    default_tz = timezone.get_current_timezone()

    for thread in threads:
        is_unread = thread_is_unread_for_user(thread, user, state_map)
        if is_unread:
            unread_count += 1
        if len(preview_rows) >= limit:
            continue
        preview_rows.append(
            {
                'id': thread.id,
                'title': title_fn(thread),
                'meta': meta_fn(thread),
                'body': thread.last_message_excerpt,
                'is_unread': is_unread,
                'created_at_label': timezone.localtime(
                    thread.last_message_at,
                    ZoneInfo(thread.clinic.timezone or 'UTC') if thread.clinic_id else default_tz,
                ).strftime('%b %d, %I:%M %p'),
                'url': reverse('messages-thread', args=[thread.id]),
            }
        )
    return unread_count, preview_rows
