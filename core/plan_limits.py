from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db import DatabaseError
from django.utils import timezone

from .models import Appointment, AppointmentType, ClinicSubscription, Staff


def get_current_subscription(clinic):
    """Plan notes: prefer the latest active subscription before any pending billing attempt."""
    try:
        active_subscription = (
            ClinicSubscription.objects.filter(
                clinic=clinic,
                status=ClinicSubscription.Status.ACTIVE,
            )
            .select_related('plan')
            .order_by('-created_at')
            .first()
        )
    except DatabaseError:
        return None
    if active_subscription:
        return active_subscription
    try:
        return (
            ClinicSubscription.objects.filter(clinic=clinic)
            .select_related('plan')
            .order_by('-created_at')
            .first()
        )
    except DatabaseError:
        return None


def get_clinic_plan(clinic):
    """Plan notes: all freemium checks resolve the effective plan through this helper."""
    subscription = get_current_subscription(clinic)
    return subscription.plan if subscription else None


def _pluralize(label: str, value: int) -> str:
    return label if value == 1 else f'{label}s'


def _usage_item(*, label: str, used: int, limit: int | None):
    """Plan notes: null limits are treated as unlimited Premium capacity."""
    is_unlimited = limit is None
    remaining = None if is_unlimited else max(limit - used, 0)
    percent_used = 0 if is_unlimited or not limit else min(100, round((used / limit) * 100))
    is_at_limit = not is_unlimited and used >= limit
    is_near_limit = not is_unlimited and not is_at_limit and percent_used >= 80

    if is_unlimited:
        remaining_label = f'Unlimited {_pluralize(label, 2)}'
        summary_label = f'{used} {_pluralize(label, used)} in use'
    else:
        remaining_label = f'{remaining} {_pluralize(label, remaining)} left'
        summary_label = f'{used} of {limit} {_pluralize(label, limit)} used'

    return {
        'label': label,
        'used': used,
        'limit': limit,
        'remaining': remaining,
        'is_unlimited': is_unlimited,
        'percent_used': percent_used,
        'is_at_limit': is_at_limit,
        'is_near_limit': is_near_limit,
        'summary_label': summary_label,
        'remaining_label': remaining_label,
    }


def _appointment_month_window(clinic):
    tz = ZoneInfo(clinic.timezone or 'UTC')
    current_local = timezone.localtime(timezone.now(), tz)
    start_local = current_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_local.month == 12:
        next_month_local = start_local.replace(year=start_local.year + 1, month=1)
    else:
        next_month_local = start_local.replace(month=start_local.month + 1)
    return tz, start_local, next_month_local


def clinic_usage_summary(clinic):
    """Plan notes: compute plan-backed limits and feature flags in one place."""
    subscription = get_current_subscription(clinic)
    plan = subscription.plan if subscription else None
    tz, month_start_local, next_month_local = _appointment_month_window(clinic)
    month_start = month_start_local.astimezone(dt_timezone.utc)
    next_month = next_month_local.astimezone(dt_timezone.utc)

    try:
        staff_used = Staff.objects.filter(clinic=clinic, is_active=True).count()
        service_used = AppointmentType.objects.filter(clinic=clinic, is_active=True).count()
        appointment_used = (
            Appointment.objects.filter(
                clinic=clinic,
                start_at__gte=month_start,
                start_at__lt=next_month,
            )
            .exclude(status=Appointment.Status.CANCELLED)
            .count()
        )
    except DatabaseError:
        staff_used = 0
        service_used = 0
        appointment_used = 0

    staff_limit = plan.staff_limit if plan else None
    service_limit = plan.service_limit if plan else None
    appointment_limit = plan.monthly_appointment_limit if plan else None

    return {
        'subscription': subscription,
        'plan': plan,
        'has_plan': plan is not None,
        'plan_name': plan.name if plan else 'No plan selected',
        'plan_price_label': (
            f'{plan.currency} {plan.price_dollars:.2f} / {plan.interval}'
            if plan
            else 'No active billing record'
        ),
        'is_free': bool(plan and plan.is_free),
        'is_premium': bool(plan and not plan.is_free),
        'reminders_enabled': True if not plan else plan.includes_reminders,
        'notifications_enabled': True if not plan else plan.includes_notifications,
        'messaging_enabled': True if not plan else plan.includes_messaging,
        'waitlist_enabled': True if not plan else plan.includes_waitlist,
        'custom_branding_enabled': True if not plan else plan.includes_custom_branding,
        'month_window_label': month_start_local.strftime('%B %Y'),
        'staff': _usage_item(label='staff seat', used=staff_used, limit=staff_limit),
        'services': _usage_item(label='service', used=service_used, limit=service_limit),
        'appointments': _usage_item(
            label='appointment',
            used=appointment_used,
            limit=appointment_limit,
        ),
        'subscription_status': subscription.status if subscription else '',
        'subscription_is_active': bool(
            subscription and subscription.status == ClinicSubscription.Status.ACTIVE
        ),
        'timezone': tz.key if hasattr(tz, 'key') else str(tz),
    }


def clinic_can_add_staff(clinic, *, usage=None) -> bool:
    """Plan notes: staff creation checks should call this instead of hardcoding Free limits."""
    usage = usage or clinic_usage_summary(clinic)
    item = usage['staff']
    return item['is_unlimited'] or item['remaining'] > 0


def clinic_can_add_service(clinic, *, usage=None) -> bool:
    """Plan notes: service catalog growth is capped here for the Free tier."""
    usage = usage or clinic_usage_summary(clinic)
    item = usage['services']
    return item['is_unlimited'] or item['remaining'] > 0


def clinic_can_accept_appointment(clinic, *, usage=None) -> bool:
    """Plan notes: bookings and walk-ins share the same monthly appointment quota."""
    usage = usage or clinic_usage_summary(clinic)
    item = usage['appointments']
    return item['is_unlimited'] or item['remaining'] > 0


def clinic_can_send_reminders(clinic, *, usage=None) -> bool:
    """Plan notes: reminder emails stay behind a plan flag instead of view-level branching."""
    usage = usage or clinic_usage_summary(clinic)
    return usage['reminders_enabled']


def clinic_can_use_notifications(clinic, *, usage=None) -> bool:
    """Plan notes: notification storage and bell visibility should resolve through this flag."""
    usage = usage or clinic_usage_summary(clinic)
    return usage['notifications_enabled']


def clinic_can_use_messaging(clinic, *, usage=None) -> bool:
    """Plan notes: secure patient/staff inbox access is Premium-only once plans are enforced."""
    usage = usage or clinic_usage_summary(clinic)
    return usage['messaging_enabled']


def clinic_can_use_waitlist(clinic, *, usage=None) -> bool:
    """Plan notes: waitlist capture and the staff queue should resolve through one flag."""
    usage = usage or clinic_usage_summary(clinic)
    return usage['waitlist_enabled']


def clinic_can_use_custom_branding(clinic, *, usage=None) -> bool:
    """Plan notes: future branding settings should check this capability before saving changes."""
    usage = usage or clinic_usage_summary(clinic)
    return usage['custom_branding_enabled']
