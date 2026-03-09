from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ClinicSubscription


PAYPAL_STATUS_MAP = {
    'APPROVAL_PENDING': ClinicSubscription.Status.PENDING,
    'APPROVED': ClinicSubscription.Status.PENDING,
    'ACTIVE': ClinicSubscription.Status.ACTIVE,
    'SUSPENDED': ClinicSubscription.Status.SUSPENDED,
    'CANCELLED': ClinicSubscription.Status.CANCELLED,
    'EXPIRED': ClinicSubscription.Status.EXPIRED,
}


def parse_paypal_datetime(value: str | None):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def map_paypal_status(value: str | None) -> str:
    if not value:
        return ClinicSubscription.Status.PENDING
    return PAYPAL_STATUS_MAP.get(value.upper(), ClinicSubscription.Status.PENDING)


def clinic_has_active_subscription(clinic) -> bool:
    return ClinicSubscription.objects.filter(
        clinic=clinic,
        status=ClinicSubscription.Status.ACTIVE,
    ).exists()
