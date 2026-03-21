from django.contrib.auth import get_user_model

from .models import Notification, Staff

User = get_user_model()


def _clinic_staff_recipients(clinic, *, admins_only=False):
    staff_qs = (
        Staff.objects.filter(clinic=clinic, is_active=True, user__is_active=True)
        .select_related('user')
        .order_by('user__id')
        .distinct()
    )
    if admins_only:
        admin_staff = list(staff_qs.filter(user__groups__name='Admin'))
        if admin_staff:
            return [member.user for member in admin_staff]
    return [member.user for member in staff_qs]


def create_clinic_notifications(
    clinic,
    *,
    title,
    body='',
    link='',
    event_type=Notification.EventType.GENERIC,
    level=Notification.Level.INFO,
    actor=None,
    admins_only=False,
    recipients=None,
    metadata=None,
):
    recipient_users = recipients or _clinic_staff_recipients(clinic, admins_only=admins_only)
    deduped_users = []
    seen_ids = set()
    for user in recipient_users:
        if not user or user.pk in seen_ids:
            continue
        seen_ids.add(user.pk)
        deduped_users.append(user)

    notifications = [
        Notification(
            clinic=clinic,
            recipient=user,
            actor=actor,
            event_type=event_type,
            level=level,
            title=title,
            body=body,
            link=link,
            metadata=metadata or {},
        )
        for user in deduped_users
    ]
    Notification.objects.bulk_create(notifications)
    return notifications
