import json
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class ResendEmailBackend(BaseEmailBackend):
    api_url = 'https://api.resend.com/emails'

    def __init__(self, api_key=None, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key or getattr(settings, 'RESEND_API_KEY', '')

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
            },
            method='POST',
        )

        try:
            with urllib.request.urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
                if 200 <= response.status < 300:
                    return 1
                if self.fail_silently:
                    return 0
                raise RuntimeError(f'Resend send failed with status {response.status}')
        except Exception:
            if self.fail_silently:
                return 0
            raise
