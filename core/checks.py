from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


def _is_default_secured_key(value) -> bool:
    if isinstance(value, list):
        return any(item == settings.DEFAULT_SECURED_FIELDS_KEY for item in value)
    return value == settings.DEFAULT_SECURED_FIELDS_KEY


def _is_local_only_host(host: str) -> bool:
    host = host.strip().lower()
    return host in {'localhost', '127.0.0.1', '[::1]'}


@register(Tags.security, deploy=True)
def production_security_checks(app_configs, **kwargs):
    errors = []

    if settings.SECRET_KEY in {settings.DEFAULT_SECRET_KEY, '', 'change-me'}:
        errors.append(
            Error(
                'SECRET_KEY is using an insecure default value.',
                hint='Set a unique SECRET_KEY in the environment before deploying.',
                id='core.E001',
            )
        )

    if not settings.ALLOWED_HOSTS or all(_is_local_only_host(host) for host in settings.ALLOWED_HOSTS):
        errors.append(
            Error(
                'ALLOWED_HOSTS only contains local development hosts.',
                hint='Set production hostnames before deployment.',
                id='core.E002',
            )
        )

    if not settings.DJANGO_SECURE:
        errors.append(
            Error(
                'DJANGO_SECURE is disabled.',
                hint='Set DJANGO_SECURE=true before production so HTTPS and secure cookies are enforced.',
                id='core.E003',
            )
        )

    if _is_default_secured_key(settings.SECURED_FIELDS_KEY):
        errors.append(
            Error(
                'SECURED_FIELDS_KEY is using the development fallback key.',
                hint='Generate a unique SECURED_FIELDS_KEY before deployment.',
                id='core.E004',
            )
        )

    if settings.SECURED_FIELDS_HASH_SALT in {settings.DEFAULT_SECURED_FIELDS_HASH_SALT, '', 'change-me'}:
        errors.append(
            Error(
                'SECURED_FIELDS_HASH_SALT is using a development placeholder.',
                hint='Set a unique SECURED_FIELDS_HASH_SALT before deployment.',
                id='core.E005',
            )
        )

    paypal_enabled = any(
        [
            settings.PAYPAL_CLIENT_ID,
            settings.PAYPAL_SECRET,
            settings.PAYPAL_PRODUCT_ID,
            settings.ENFORCE_SUBSCRIPTION,
        ]
    )
    if paypal_enabled and not settings.PAYPAL_VERIFY_WEBHOOK:
        errors.append(
            Error(
                'PAYPAL_VERIFY_WEBHOOK is disabled while PayPal billing is configured.',
                hint='Set PAYPAL_VERIFY_WEBHOOK=true before production billing goes live.',
                id='core.E006',
            )
        )

    if settings.PAYPAL_VERIFY_WEBHOOK and not settings.PAYPAL_WEBHOOK_ID:
        errors.append(
            Error(
                'PAYPAL_WEBHOOK_ID is missing while PAYPAL_VERIFY_WEBHOOK=true.',
                hint='Set PAYPAL_WEBHOOK_ID for webhook signature verification.',
                id='core.E007',
            )
        )

    return errors


@register(Tags.security)
def development_warning_checks(app_configs, **kwargs):
    warnings = []

    if settings.DEBUG and settings.SEND_BOOKING_CONFIRMATION:
        warnings.append(
            Warning(
                'Booking confirmations are enabled in DEBUG mode.',
                hint='Disable SEND_BOOKING_CONFIRMATION locally if you do not want test emails or real integrations firing.',
                id='core.W001',
            )
        )

    return warnings
