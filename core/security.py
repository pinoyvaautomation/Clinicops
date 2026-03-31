import logging
import hashlib
from datetime import timedelta, timezone as dt_timezone
from ipaddress import ip_address, ip_network

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models import Q
from django.dispatch import receiver
from django.conf import settings
from django.utils import timezone

from .models import SecurityAccessRule, SecurityEvent, Staff

User = get_user_model()
logger = logging.getLogger(__name__)

AUTH_THROTTLE_RULES = {
    'accounts_login': {
        'paths': ['/accounts/login/'],
        'mode': 'failure_only',
        'limit': 5,
        'window_minutes': 15,
        'lockout_minutes': 30,
        'identifier_fields': ['username'],
    },
    'admin_login': {
        'paths': ['/admin/login/'],
        'mode': 'failure_only',
        'limit': 5,
        'window_minutes': 15,
        'lockout_minutes': 30,
        'identifier_fields': ['username'],
    },
    'clinic_signup': {
        'paths': ['/signup/'],
        'mode': 'all_posts',
        'limit': 8,
        'window_minutes': 30,
        'lockout_minutes': 30,
        'identifier_fields': ['admin_email'],
    },
    'patient_signup': {
        'paths': ['/clinic/', '/patient-signup/'],
        'mode': 'all_posts',
        'limit': 8,
        'window_minutes': 30,
        'lockout_minutes': 20,
        'identifier_fields': ['email'],
    },
    'resend_verification': {
        'paths': ['/resend-verification/'],
        'mode': 'all_posts',
        'limit': 5,
        'window_minutes': 15,
        'lockout_minutes': 20,
        'identifier_fields': ['email'],
    },
    'password_reset': {
        'paths': ['/accounts/password_reset/'],
        'mode': 'all_posts',
        'limit': 5,
        'window_minutes': 15,
        'lockout_minutes': 20,
        'identifier_fields': ['email'],
    },
    'appointment_lookup': {
        'paths': ['/appointments/lookup/'],
        'mode': 'all_posts',
        'limit': 10,
        'window_minutes': 15,
        'lockout_minutes': 15,
        'identifier_fields': ['email', 'confirmation_code'],
    },
}


def _staff_clinic_for_user(user):
    """Only clinic staff sign-ins feed the clinic owner security view."""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    try:
        return user.staff.clinic
    except Staff.DoesNotExist:
        return None


def get_client_ip(request):
    if request is None:
        return ''
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return (request.META.get('REMOTE_ADDR') or '').strip()


def get_client_country(request):
    if request is None:
        return ''
    candidate = (
        request.META.get('HTTP_CF_IPCOUNTRY')
        or request.META.get('HTTP_X_COUNTRY_CODE')
        or request.META.get('HTTP_X_VERCEL_IP_COUNTRY')
        or request.META.get('HTTP_X_APPENGINE_COUNTRY')
        or ''
    ).strip().upper()
    if len(candidate) == 2 and candidate.isalpha() and candidate not in {'XX', 'T1'}:
        return candidate
    return ''


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
    event = SecurityEvent.objects.create(
        clinic=clinic if clinic is not None else _staff_clinic_for_user(user),
        user=user,
        event_type=event_type,
        identifier=(identifier or '').strip()[:254],
        ip_address=get_client_ip(request) or None,
        country_code=get_client_country(request),
        user_agent=_get_user_agent(request),
        path=getattr(request, 'path', '')[:255] if request is not None else '',
        metadata=metadata or {},
    )
    _maybe_send_security_alert(event)
    return event


def get_auth_throttle_rule(request):
    path = getattr(request, 'path', '')
    for scope, rule in AUTH_THROTTLE_RULES.items():
        if scope == 'patient_signup':
            if path.startswith('/clinic/') and path.endswith('/patient-signup/'):
                return scope, rule
            continue
        if path in rule['paths']:
            return scope, rule
    return None, None


def get_security_identifier_from_request(request, rule):
    if request is None or rule is None:
        return ''
    for field in rule.get('identifier_fields', []):
        value = (request.POST.get(field) or '').strip()
        if value:
            return value.lower() if '@' in value else value
    return ''


def _throttle_cache_key(scope, ip_value, identifier):
    base = f'{scope}|{ip_value or "-"}|{identifier or "-"}'
    digest = hashlib.sha256(base.encode('utf-8')).hexdigest()
    return f'clinicops:auth-guard:{digest}'


def _read_throttle_bucket(scope, ip_value, identifier):
    return cache.get(_throttle_cache_key(scope, ip_value, identifier)) or {}


def _write_throttle_bucket(scope, ip_value, identifier, bucket, *, timeout_seconds):
    cache.set(_throttle_cache_key(scope, ip_value, identifier), bucket, timeout_seconds)


def clear_auth_throttle(request, *identifiers):
    ip_value = get_client_ip(request)
    normalized_identifiers = {''}
    for identifier in identifiers:
        value = (identifier or '').strip()
        if value:
            normalized_identifiers.add(value.lower() if '@' in value else value)
    for scope in ('accounts_login', 'admin_login'):
        for identifier in normalized_identifiers:
            cache.delete(_throttle_cache_key(scope, ip_value, identifier))


def is_auth_request_rate_limited(request, *, scope, rule, identifier=''):
    ip_value = get_client_ip(request)
    bucket = _read_throttle_bucket(scope, ip_value, identifier)
    locked_until = bucket.get('locked_until')
    if locked_until and timezone.now().timestamp() < locked_until:
        return True
    return False


def register_auth_attempt(request, *, scope, rule, identifier='', success=False):
    ip_value = get_client_ip(request)
    now_ts = timezone.now().timestamp()
    window_seconds = int(rule['window_minutes'] * 60)
    lockout_seconds = int(rule['lockout_minutes'] * 60)
    timeout_seconds = max(window_seconds, lockout_seconds) + 60
    bucket = _read_throttle_bucket(scope, ip_value, identifier)
    attempts = [ts for ts in bucket.get('attempts', []) if now_ts - ts < window_seconds]

    if success and rule['mode'] == 'failure_only':
        cache.delete(_throttle_cache_key(scope, ip_value, identifier))
        return False

    attempts.append(now_ts)
    should_lock = len(attempts) >= int(rule['limit'])
    bucket['attempts'] = attempts
    bucket['locked_until'] = now_ts + lockout_seconds if should_lock else 0
    _write_throttle_bucket(scope, ip_value, identifier, bucket, timeout_seconds=timeout_seconds)
    return should_lock


def _rule_matches_ip(rule, ip_value):
    if not ip_value:
        return False
    try:
        client_ip = ip_address(ip_value)
        if '/' in rule.value:
            return client_ip in ip_network(rule.value, strict=False)
        return str(client_ip) == rule.value
    except ValueError:
        return False


def _rule_matches_country(rule, country_code):
    return bool(country_code) and rule.value == country_code


def resolve_security_access(request, *, auth_only=False):
    ip_value = get_client_ip(request)
    country_code = get_client_country(request)
    if not ip_value and not country_code:
        return None, None

    matching_allow = None
    matching_block = None
    rules = SecurityAccessRule.objects.filter(is_active=True).order_by('action', 'id')
    for rule in rules:
        if auth_only and rule.scope == SecurityAccessRule.Scope.GLOBAL:
            applies = True
        elif auth_only:
            applies = rule.scope == SecurityAccessRule.Scope.AUTH
        else:
            applies = rule.scope == SecurityAccessRule.Scope.GLOBAL
        if not applies:
            continue

        matched = False
        if rule.target_type == SecurityAccessRule.TargetType.IP:
            matched = _rule_matches_ip(rule, ip_value)
        elif rule.target_type == SecurityAccessRule.TargetType.COUNTRY:
            matched = _rule_matches_country(rule, country_code)

        if not matched:
            continue
        if rule.action == SecurityAccessRule.Action.ALLOW and matching_allow is None:
            matching_allow = rule
        elif rule.action == SecurityAccessRule.Action.BLOCK and matching_block is None:
            matching_block = rule

    if matching_allow:
        return matching_allow, None
    return None, matching_block


def _security_alert_recipients():
    if settings.SECURITY_ALERT_EMAILS:
        return settings.SECURITY_ALERT_EMAILS
    return list(
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )


def _security_alert_cache_key(event: SecurityEvent):
    base = '|'.join(
        [
            event.event_type,
            event.ip_address or '-',
            event.country_code or '-',
            event.path or '-',
            str(event.metadata.get('scope') or '-'),
            str(event.metadata.get('target_value') or '-'),
        ]
    )
    digest = hashlib.sha256(base.encode('utf-8')).hexdigest()
    return f'clinicops:security-alert:{digest}'


def _security_alert_subject(event: SecurityEvent):
    label = event.get_event_type_display()
    ip_label = event.ip_address or 'unknown IP'
    country_label = f' ({event.country_code})' if event.country_code else ''
    return f'[ClinicOps Security] {label} from {ip_label}{country_label}'


def _security_alert_body(event: SecurityEvent):
    clinic_name = event.clinic.name if event.clinic_id else 'No linked clinic'
    user_label = ''
    if event.user_id:
        user_label = event.user.get_full_name().strip() or event.user.email or event.user.username
    user_label = user_label or event.identifier or 'Unknown account'
    metadata_lines = []
    for key, value in sorted((event.metadata or {}).items()):
        metadata_lines.append(f'- {key}: {value}')

    body_lines = [
        'ClinicOps security alert',
        '',
        f'Event: {event.get_event_type_display()}',
        f'Time (UTC): {event.created_at.astimezone(dt_timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")}',
        f'Clinic: {clinic_name}',
        f'User / identifier: {user_label}',
        f'IP address: {event.ip_address or "-"}',
        f'Country: {event.country_code or "-"}',
        f'Path: {event.path or "-"}',
        f'User agent: {event.user_agent or "-"}',
    ]
    if metadata_lines:
        body_lines.extend(['', 'Metadata:'])
        body_lines.extend(metadata_lines)
    return '\n'.join(body_lines)


def _maybe_send_security_alert(event: SecurityEvent):
    if event.event_type not in {
        SecurityEvent.EventType.RATE_LIMITED,
        SecurityEvent.EventType.ACCESS_BLOCKED,
    }:
        return

    recipients = _security_alert_recipients()
    if not recipients:
        return

    cache_key = _security_alert_cache_key(event)
    cooldown_seconds = max(60, int(settings.SECURITY_ALERT_COOLDOWN_MINUTES * 60))
    if cache.get(cache_key):
        return
    cache.set(cache_key, True, cooldown_seconds)

    try:
        send_mail(
            _security_alert_subject(event),
            _security_alert_body(event),
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=False,
        )
    except Exception:
        logger.exception('Security alert email failed for event=%s', event.id)


@receiver(user_logged_in)
def log_successful_login(sender, request, user, **kwargs):
    clear_auth_throttle(request, user.email, user.username)
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
