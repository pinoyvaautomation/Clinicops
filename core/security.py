from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models import Q
from django.dispatch import receiver

from .models import SecurityEvent, Staff

User = get_user_model()


def _staff_clinic_for_user(user):
    """Only clinic staff sign-ins feed the clinic owner security view."""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    try:
        return user.staff.clinic
    except Staff.DoesNotExist:
        return None


def _get_client_ip(request):
    if request is None:
        return ''
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR') or '').strip()


def _get_user_agent(request):
    if request is None:
        return ''
    return (request.META.get('HTTP_USER_AGENT') or '').strip()[:255]


def find_user_for_security_identifier(identifier):
    """Resolve a posted login identifier to a local user when possible."""
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    return (
        User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier))
        .order_by('id')
        .first()
    )


def log_security_event(*, event_type, request=None, user=None, clinic=None, identifier='', metadata=None):
    """Create a normalized security audit event with request metadata."""
    SecurityEvent.objects.create(
        clinic=clinic if clinic is not None else _staff_clinic_for_user(user),
        user=user,
        event_type=event_type,
        identifier=(identifier or '').strip()[:254],
        ip_address=_get_client_ip(request) or None,
        user_agent=_get_user_agent(request),
        path=getattr(request, 'path', '')[:255] if request is not None else '',
        metadata=metadata or {},
    )


@receiver(user_logged_in)
def log_successful_login(sender, request, user, **kwargs):
    log_security_event(
        event_type=SecurityEvent.EventType.LOGIN_SUCCESS,
        request=request,
        user=user,
        identifier=user.email or user.username,
    )


@receiver(user_logged_out)
def log_logout_event(sender, request, user, **kwargs):
    if not user:
        return
    log_security_event(
        event_type=SecurityEvent.EventType.LOGOUT,
        request=request,
        user=user,
        identifier=user.email or user.username,
    )
