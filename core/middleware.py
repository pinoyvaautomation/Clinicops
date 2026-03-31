from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.shortcuts import render

from .models import SecurityEvent
from .security import (
    find_user_for_security_identifier,
    get_auth_throttle_rule,
    get_security_identifier_from_request,
    is_auth_request_rate_limited,
    log_security_event,
    register_auth_attempt,
    resolve_security_access,
)


class SecurityAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        auth_scope, auth_rule = get_auth_throttle_rule(request)
        is_auth_sensitive = auth_scope is not None
        allow_rule, block_rule = resolve_security_access(request, auth_only=is_auth_sensitive)
        identifier = get_security_identifier_from_request(request, auth_rule)
        matched_user = find_user_for_security_identifier(identifier) if identifier else None

        if block_rule and not allow_rule:
            log_security_event(
                event_type=SecurityEvent.EventType.ACCESS_BLOCKED,
                request=request,
                user=matched_user,
                identifier=identifier,
                metadata={
                    'rule_id': block_rule.id,
                    'rule_name': block_rule.name,
                    'rule_scope': block_rule.scope,
                    'target_type': block_rule.target_type,
                    'target_value': block_rule.value,
                },
            )
            return self._blocked_response(
                request,
                status=403,
                title='Access blocked',
                message='Access from this network is blocked for ClinicOps security reasons.',
            )

        if (
            request.method == 'POST'
            and auth_scope
            and not allow_rule
            and is_auth_request_rate_limited(
                request,
                scope=auth_scope,
                rule=auth_rule,
                identifier=identifier,
            )
        ):
            log_security_event(
                event_type=SecurityEvent.EventType.RATE_LIMITED,
                request=request,
                user=matched_user,
                identifier=identifier,
                metadata={
                    'scope': auth_scope,
                    'mode': auth_rule['mode'],
                    'reason': 'locked',
                },
            )
            return self._blocked_response(
                request,
                status=429,
                title='Too many attempts',
                message='Too many attempts were detected from this network. Please wait and try again later.',
            )

        response = self.get_response(request)

        if request.method != 'POST' or not auth_scope or allow_rule:
            return response

        is_success = bool(
            auth_rule['mode'] == 'failure_only'
            and response.status_code in {301, 302, 303, 307, 308}
            and request.session.get('_auth_user_id')
        )

        if auth_scope == 'admin_login' and not is_success:
            log_security_event(
                event_type=SecurityEvent.EventType.LOGIN_FAILED,
                request=request,
                user=matched_user,
                identifier=identifier,
                metadata={'scope': auth_scope, 'source': 'admin_login'},
            )

        locked_now = register_auth_attempt(
            request,
            scope=auth_scope,
            rule=auth_rule,
            identifier=identifier,
            success=is_success,
        )
        if locked_now:
            log_security_event(
                event_type=SecurityEvent.EventType.RATE_LIMITED,
                request=request,
                user=matched_user,
                identifier=identifier,
                metadata={
                    'scope': auth_scope,
                    'mode': auth_rule['mode'],
                    'reason': 'threshold_reached',
                },
            )

        return response

    def _blocked_response(self, request, *, status, title, message):
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return HttpResponse(message, status=status)
        if not hasattr(request, 'user'):
            request.user = AnonymousUser()
        return render(
            request,
            'security_guard.html',
            {
                'title': title,
                'message': message,
                'status_code': status,
            },
            status=status,
        )
