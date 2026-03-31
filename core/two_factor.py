import base64
import hashlib
from binascii import unhexlify
from io import BytesIO

import qrcode
from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.utils.crypto import get_random_string
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import TwoFactorRecoveryCode

User = get_user_model()

TWO_FACTOR_DEVICE_NAME = 'ClinicOps Authenticator'
TWO_FACTOR_BACKEND_SESSION_KEY = 'two_factor_auth_backend'


def user_can_manage_two_factor(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return user.is_superuser or (
        settings.TWO_FACTOR_ALLOW_CLINIC_ADMINS
        and user.groups.filter(name='Admin').exists()
    )


def user_has_confirmed_two_factor(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def user_requires_two_factor_setup(user):
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and user.is_superuser
        and settings.TWO_FACTOR_SUPERUSERS_REQUIRED
        and not user_has_confirmed_two_factor(user)
    )


def user_requires_two_factor_verification(user):
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and user_has_confirmed_two_factor(user)
        and not user.is_verified()
    )


def get_confirmed_totp_device(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return (
        TOTPDevice.objects.filter(user=user, confirmed=True)
        .order_by('-id')
        .first()
    )


def get_or_create_setup_device(user):
    device = (
        TOTPDevice.objects.filter(user=user, confirmed=False)
        .order_by('-id')
        .first()
    )
    if device:
        return device
    return TOTPDevice.objects.create(
        user=user,
        name=TWO_FACTOR_DEVICE_NAME,
        confirmed=False,
    )


def build_qr_data_uri(config_url):
    image = qrcode.make(config_url)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{encoded}'


def manual_entry_secret(device):
    return base64.b32encode(unhexlify(device.key)).decode('ascii').rstrip('=')


def _normalize_recovery_code(code):
    return ''.join((code or '').upper().split()).replace('-', '')


def _recovery_code_hash(code):
    return hashlib.sha256(code.encode('utf-8')).hexdigest()


def generate_recovery_codes(user, *, count=None):
    count = count or settings.TWO_FACTOR_RECOVERY_CODE_COUNT
    TwoFactorRecoveryCode.objects.filter(user=user).delete()

    plain_codes = []
    objects = []
    for _ in range(count):
        raw = get_random_string(10, allowed_chars='23456789ABCDEFGHJKLMNPQRSTUVWXYZ')
        plain = f'{raw[:5]}-{raw[5:]}'
        normalized = _normalize_recovery_code(plain)
        plain_codes.append(plain)
        objects.append(
            TwoFactorRecoveryCode(
                user=user,
                code_hash=_recovery_code_hash(normalized),
                code_suffix=normalized[-4:],
            )
        )
    TwoFactorRecoveryCode.objects.bulk_create(objects)
    return plain_codes


def consume_recovery_code(user, code):
    normalized = _normalize_recovery_code(code)
    if not normalized:
        return None
    code_hash = _recovery_code_hash(normalized)
    return (
        TwoFactorRecoveryCode.objects.filter(
            user=user,
            code_hash=code_hash,
            consumed_at__isnull=True,
        )
        .order_by('id')
        .first()
    )


def reset_two_factor_for_user(user):
    TOTPDevice.objects.filter(user=user).delete()
    TwoFactorRecoveryCode.objects.filter(user=user).delete()


def recovery_code_count(user):
    return TwoFactorRecoveryCode.objects.filter(user=user, consumed_at__isnull=True).count()


def finish_two_factor_login(request, *, device):
    user = request.user
    if not getattr(user, 'is_authenticated', False):
        backend = request.session.get(TWO_FACTOR_BACKEND_SESSION_KEY)
        if backend and getattr(user, 'backend', None) != backend:
            user.backend = backend
        elif not getattr(user, 'backend', None):
            user.backend = settings.AUTHENTICATION_BACKENDS[0]
        auth_login(request, user, backend=user.backend)
    otp_login(request, device)


def post_two_factor_redirect(request):
    next_url = request.session.pop('two_factor_redirect_to', '')
    return next_url or settings.LOGIN_REDIRECT_URL
