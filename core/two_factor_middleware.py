from django.shortcuts import redirect
from django.urls import reverse

from .two_factor import (
    user_requires_two_factor_setup,
    user_requires_two_factor_verification,
)


class TwoFactorEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_paths = {
            reverse('login'),
            reverse('logout'),
            reverse('password_reset'),
            reverse('two-factor-setup'),
            reverse('two-factor-verify'),
            reverse('two-factor-recovery-codes'),
            reverse('two-factor-regenerate'),
            reverse('two-factor-disable'),
            reverse('admin-login'),
            '/admin/logout/',
        }

    def __call__(self, request):
        user = getattr(request, 'user', None)
        path = getattr(request, 'path', '')
        if not user or not user.is_authenticated:
            return self.get_response(request)

        if path in self.exempt_paths:
            return self.get_response(request)

        if user_requires_two_factor_setup(user):
            return redirect('two-factor-setup')

        if user_requires_two_factor_verification(user):
            return redirect('two-factor-verify')

        return self.get_response(request)
