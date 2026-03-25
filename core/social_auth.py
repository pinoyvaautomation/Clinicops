from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.urls import reverse

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

User = get_user_model()


def find_matching_local_user(email: str | None):
    """Return an existing ClinicOps user that matches the Google email."""
    normalized_email = (email or '').strip().lower()
    if not normalized_email:
        return None
    return (
        User.objects.filter(email__iexact=normalized_email).first()
        or User.objects.filter(username__iexact=normalized_email).first()
    )


class ClinicSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Allow Google sign-in only when it matches an existing ClinicOps account."""

    def _abort_login(self, request, message: str):
        """Redirect the user back to the branded login page with a clear message."""
        messages.error(request, message)
        raise ImmediateHttpResponse(redirect(reverse('login')))

    def can_authenticate_by_email(self, login, email):
        """Limit Google email authentication to active accounts that already exist locally."""
        user = find_matching_local_user(email)
        return bool(user and user.is_active)

    def pre_social_login(self, request, sociallogin):
        """Block auto-signup and inactive accounts before allauth finalizes the login."""
        if sociallogin.account.provider != 'google':
            return
        if sociallogin.is_existing:
            if sociallogin.user and not sociallogin.user.is_active:
                self._abort_login(request, 'Please verify your email before signing in with Google.')
            return

        email = getattr(sociallogin.user, 'email', '')
        if not email:
            self._abort_login(request, 'Google did not return an email address. Please sign in with your password instead.')

        user = find_matching_local_user(email)
        if user is None:
            self._abort_login(
                request,
                'Google sign-in only works for existing ClinicOps accounts. Use clinic signup or staff setup first.',
            )
        if not user.is_active:
            self._abort_login(request, 'Please verify your email before signing in with Google.')

    def is_open_for_signup(self, request, sociallogin):
        """Disable provider-driven signups until ClinicOps defines a full OAuth onboarding flow."""
        return False
