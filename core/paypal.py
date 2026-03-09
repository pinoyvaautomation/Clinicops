import base64
import json
import ssl
from urllib import parse, request

from django.conf import settings


class PayPalError(Exception):
    pass


def _ssl_context():
    if not settings.PAYPAL_SSL_VERIFY:
        return ssl._create_unverified_context()

    if settings.PAYPAL_CA_BUNDLE:
        return ssl.create_default_context(cafile=settings.PAYPAL_CA_BUNDLE)

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _request_json(url: str, data: dict, access_token: str):
    payload = json.dumps(data).encode('utf-8')
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    req = request.Request(url, data=payload, headers=headers, method='POST')
    with request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _get_json(url: str, access_token: str):
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    req = request.Request(url, headers=headers, method='GET')
    with request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _basic_auth_header(client_id: str, secret: str) -> str:
    token = base64.b64encode(f'{client_id}:{secret}'.encode('utf-8')).decode('utf-8')
    return f'Basic {token}'


def get_access_token() -> str:
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_SECRET:
        raise PayPalError('PayPal client ID/secret not configured.')

    url = f"{settings.PAYPAL_API_BASE}/v1/oauth2/token"
    data = parse.urlencode({'grant_type': 'client_credentials'}).encode('utf-8')
    headers = {
        'Authorization': _basic_auth_header(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_SECRET),
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    req = request.Request(url, data=data, headers=headers, method='POST')
    try:
        with request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        raise PayPalError(f'Failed to fetch PayPal access token: {exc}') from exc

    token = payload.get('access_token')
    if not token:
        raise PayPalError('PayPal access token missing in response.')
    return token


def verify_webhook_signature(headers, event_body: dict) -> bool:
    if not settings.PAYPAL_VERIFY_WEBHOOK:
        return True
    if not settings.PAYPAL_WEBHOOK_ID:
        raise PayPalError('PAYPAL_WEBHOOK_ID is required for webhook verification.')

    required_headers = {
        'paypal-auth-algo': headers.get('PayPal-Auth-Algo') or headers.get('PAYPAL-AUTH-ALGO'),
        'paypal-cert-url': headers.get('PayPal-Cert-Url') or headers.get('PAYPAL-CERT-URL'),
        'paypal-transmission-id': headers.get('PayPal-Transmission-Id') or headers.get('PAYPAL-TRANSMISSION-ID'),
        'paypal-transmission-sig': headers.get('PayPal-Transmission-Sig') or headers.get('PAYPAL-TRANSMISSION-SIG'),
        'paypal-transmission-time': headers.get('PayPal-Transmission-Time') or headers.get('PAYPAL-TRANSMISSION-TIME'),
    }

    if not all(required_headers.values()):
        return False

    payload = {
        'auth_algo': required_headers['paypal-auth-algo'],
        'cert_url': required_headers['paypal-cert-url'],
        'transmission_id': required_headers['paypal-transmission-id'],
        'transmission_sig': required_headers['paypal-transmission-sig'],
        'transmission_time': required_headers['paypal-transmission-time'],
        'webhook_id': settings.PAYPAL_WEBHOOK_ID,
        'webhook_event': event_body,
    }

    access_token = get_access_token()
    url = f"{settings.PAYPAL_API_BASE}/v1/notifications/verify-webhook-signature"
    data = json.dumps(payload).encode('utf-8')
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    req = request.Request(url, data=data, headers=headers, method='POST')
    try:
        with request.urlopen(req, timeout=10, context=_ssl_context()) as resp:
            response_payload = json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        raise PayPalError(f'Failed to verify webhook signature: {exc}') from exc

    return response_payload.get('verification_status') == 'SUCCESS'


def create_product(name: str, description: str | None = None) -> str:
    access_token = get_access_token()
    url = f"{settings.PAYPAL_API_BASE}/v1/catalogs/products"
    payload = {
        'name': name,
        'type': 'SERVICE',
        'category': 'SOFTWARE',
    }
    if description:
        payload['description'] = description

    try:
        response = _request_json(url, payload, access_token)
    except Exception as exc:
        raise PayPalError(f'Failed to create PayPal product: {exc}') from exc

    product_id = response.get('id')
    if not product_id:
        raise PayPalError('PayPal product ID missing in response.')
    return product_id


def create_plan(
    product_id: str,
    name: str,
    interval_unit: str,
    price_value: str,
    currency_code: str = 'USD',
) -> str:
    access_token = get_access_token()
    url = f"{settings.PAYPAL_API_BASE}/v1/billing/plans"
    payload = {
        'product_id': product_id,
        'name': name,
        'billing_cycles': [
            {
                'frequency': {'interval_unit': interval_unit, 'interval_count': 1},
                'tenure_type': 'REGULAR',
                'sequence': 1,
                'total_cycles': 0,
                'pricing_scheme': {
                    'fixed_price': {
                        'value': price_value,
                        'currency_code': currency_code,
                    }
                },
            }
        ],
        'payment_preferences': {
            'auto_bill_outstanding': True,
            'setup_fee_failure_action': 'CONTINUE',
            'payment_failure_threshold': 3,
        },
    }

    try:
        response = _request_json(url, payload, access_token)
    except Exception as exc:
        raise PayPalError(f'Failed to create PayPal plan: {exc}') from exc

    plan_id = response.get('id')
    if not plan_id:
        raise PayPalError('PayPal plan ID missing in response.')
    return plan_id


def get_subscription(subscription_id: str) -> dict:
    access_token = get_access_token()
    url = f"{settings.PAYPAL_API_BASE}/v1/billing/subscriptions/{subscription_id}"
    try:
        return _get_json(url, access_token)
    except Exception as exc:
        raise PayPalError(f'Failed to fetch PayPal subscription: {exc}') from exc
