import json
import logging
import os
import ssl
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

try:
    import certifi
except Exception:  # pragma: no cover - optional dependency
    certifi = None


class ResendEmailBackend(BaseEmailBackend):
    api_url = 'https://api.resend.com/emails'

    def __init__(self, api_key=None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key or getattr(settings, 'RESEND_API_KEY', '')
        self._logger = logging.getLogger(__name__)

    def _debug_enabled(self):
        debug_setting = getattr(settings, 'RESEND_DEBUG', None)
        if debug_setting is None:
            debug_setting = os.environ.get('RESEND_DEBUG', 'false')
        return str(debug_setting).lower() in {'1', 'true', 'yes'}

    def _user_agent(self):
        user_agent = getattr(settings, 'RESEND_USER_AGENT', None)
        if not user_agent:
            user_agent = os.environ.get('RESEND_USER_AGENT')
        return user_agent or 'ClinicOps/1.0'

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if not self.api_key:
            if self.fail_silently:
                return 0
            raise ValueError('RESEND_API_KEY is not set')

        sent = 0
        for message in email_messages:
            sent += self._send(message)
        return sent

    def _send(self, message):
        if not message.recipients():
            return 0

        payload = {
            'from': message.from_email or settings.DEFAULT_FROM_EMAIL,
            'to': message.to,
            'subject': message.subject or '',
            'text': message.body or '',
        }

        html = None
        for content, mimetype in getattr(message, 'alternatives', []) or []:
            if mimetype == 'text/html':
                html = content
                break
        if html:
            payload['html'] = html

        if message.reply_to:
            payload['reply_to'] = message.reply_to
        if message.cc:
            payload['cc'] = message.cc
        if message.bcc:
            payload['bcc'] = message.bcc

        data = json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            self.api_url,
            data=data,
            headers={
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
                'User-Agent': self._user_agent(),
            },
            method='POST',
        )

        try:
            verify_setting = getattr(settings, 'RESEND_SSL_VERIFY', None)
            if verify_setting is None:
                verify_setting = os.environ.get('RESEND_SSL_VERIFY', 'true')
            verify_ssl = str(verify_setting).lower() == 'true'

            if not verify_ssl:
                context = ssl._create_unverified_context()
            else:
                cafile = (
                    getattr(settings, 'RESEND_CA_BUNDLE', None)
                    or os.environ.get('RESEND_CA_BUNDLE')
                    or os.environ.get('SSL_CERT_FILE')
                )
                if cafile:
                    context = ssl.create_default_context(cafile=cafile)
                elif certifi is not None:
                    context = ssl.create_default_context(cafile=certifi.where())
                else:
                    context = ssl.create_default_context()
            with urllib.request.urlopen(
                request, timeout=settings.EMAIL_TIMEOUT, context=context
            ) as response:
                body = response.read()
                if self._debug_enabled():
                    preview = body[:500].decode('utf-8', errors='replace') if body else ''
                    self._logger.info(
                        'Resend response status=%s to=%s subject=%s body=%s',
                        response.status,
                        message.to,
                        message.subject,
                        preview,
                    )
                if 200 <= response.status < 300:
                    return 1
                if self.fail_silently:
                    return 0
                raise RuntimeError(f'Resend send failed with status {response.status}')
        except urllib.error.HTTPError as exc:
            if self._debug_enabled():
                body = exc.read()
                preview = body[:500].decode('utf-8', errors='replace') if body else ''
                self._logger.error(
                    'Resend HTTPError status=%s to=%s subject=%s body=%s',
                    exc.code,
                    message.to,
                    message.subject,
                    preview,
                )
            if self.fail_silently:
                return 0
            raise
        except Exception:
            if self._debug_enabled():
                self._logger.exception('Resend send failed for to=%s subject=%s', message.to, message.subject)
            if self.fail_silently:
                return 0
            raise
