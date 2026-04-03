from datetime import date, datetime, timedelta
import hashlib
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import logging
import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordResetConfirmView
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core import signing
from django.conf import settings
from django.db import transaction, DatabaseError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .booking import build_available_slots, parse_slot_value
from .forms import (
    AppointmentLookupForm,
    AppointmentSelfServiceCancelForm,
    AppointmentSelfServiceIntakeForm,
    AppointmentSelfServiceRescheduleForm,
    AppointmentUpdateForm,
    AppointmentFrontDeskUpdateForm,
    PatientUpdateForm,
    BookingForm,
    ClinicSignupForm,
    PatientSignupForm,
    ResendVerificationForm,
    AvatarUploadForm,
    WalkInAppointmentForm,
    StaffMemberCreateForm,
    StaffMemberUpdateForm,
    AppointmentTypeForm,
    ClinicAuthenticationForm,
    MessagingRolePermissionForm,
    MessageComposeForm,
    MessageReplyForm,
    TwoFactorTokenForm,
    WaitlistEntryForm,
)
from .models import (
    Appointment,
    AppointmentType,
    Clinic,
    ClinicMessagingPermission,
    ClinicSubscription,
    Message,
    MessageThread,
    Notification,
    PayPalWebhookEvent,
    Patient,
    Plan,
    SecurityEvent,
    Staff,
    WaitlistEntry,
)
from .messaging import (
    append_message,
    build_thread_preview_rows,
    clinic_messaging_access_map,
    clinic_owner_user,
    create_patient_portal_thread,
    get_or_create_appointment_thread,
    mark_thread_read,
    message_threads_for_patient,
    message_threads_for_staff,
    messaging_role_rows,
    patient_display_name,
    read_map_for_user,
    staff_display_name,
    thread_meta_for_patient,
    thread_meta_for_staff,
    thread_is_unread_for_user,
    thread_title_for_patient,
    thread_title_for_staff,
    user_can_manage_messaging_settings,
    user_can_reply_messages,
    user_can_view_messages,
    user_is_clinic_owner,
)
from .notifications import create_clinic_notifications
from .plan_limits import (
    clinic_can_accept_appointment,
    clinic_can_add_service,
    clinic_can_add_staff,
    clinic_can_send_reminders,
    clinic_can_use_notifications,
    clinic_usage_summary,
    get_current_subscription,
)
from .paypal import PayPalError, get_subscription, verify_webhook_signature
from .security import find_user_for_security_identifier, log_security_event
from .subscriptions import clinic_has_active_subscription, map_paypal_status, parse_paypal_datetime
from .two_factor import (
    TWO_FACTOR_BACKEND_SESSION_KEY,
    build_qr_data_uri,
    consume_recovery_code,
    finish_two_factor_login,
    generate_recovery_codes,
    get_confirmed_totp_device,
    get_or_create_setup_device,
    manual_entry_secret,
    post_two_factor_redirect,
    recovery_code_count,
    reset_two_factor_for_user,
    user_can_manage_two_factor,
    user_has_confirmed_two_factor,
    user_requires_two_factor_setup,
)

User = get_user_model()

ALLOWED_GROUPS = {'Admin', 'Doctor', 'Nurse', 'FrontDesk'}
logger = logging.getLogger(__name__)
_UNSET = object()


class ClinicLoginView(LoginView):
    authentication_form = ClinicAuthenticationForm
    template_name = 'registration/login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('login_eyebrow', 'Clinic Portal Access')
        context.setdefault('login_hero_title', context.get('public_brand_name', 'ClinicOps'))
        context.setdefault(
            'login_hero_text',
            'Appointments, patients, staff, reminders, and subscription control all live in one operating system for the clinic. Sign in to continue where the team left off.',
        )
        context.setdefault('login_heading', 'Sign in')
        context.setdefault(
            'login_description',
            'Use your clinic or patient portal account credentials to continue. Email login still works for accounts set up that way.',
        )
        context.setdefault('login_action_url', reverse('login'))
        context.setdefault('show_google_signin', settings.GOOGLE_OAUTH_ENABLED)
        context.setdefault('show_password_reset_link', True)
        context.setdefault('show_resend_verification_link', True)
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        self.request.session[TWO_FACTOR_BACKEND_SESSION_KEY] = getattr(user, 'backend', '')
        self.request.session['two_factor_redirect_to'] = self.get_success_url()

        if user_requires_two_factor_setup(user):
            messages.warning(
                self.request,
                'Authenticator app setup is required before this account can continue.',
            )
            return redirect('two-factor-setup')

        if user_has_confirmed_two_factor(user):
            messages.info(
                self.request,
                'Enter the authenticator code or a recovery code to finish signing in.',
            )
            return redirect('two-factor-verify')

        self.request.session.pop('two_factor_redirect_to', None)
        self.request.session.pop(TWO_FACTOR_BACKEND_SESSION_KEY, None)
        return response

    def form_invalid(self, form):
        identifier = (self.request.POST.get('username') or '').strip()
        if identifier:
            matched_user = find_user_for_security_identifier(identifier)
            log_security_event(
                event_type=SecurityEvent.EventType.LOGIN_FAILED,
                request=self.request,
                user=matched_user,
                identifier=identifier.lower() if '@' in identifier else identifier,
                metadata={'error_fields': sorted(form.errors.keys())},
            )
        return super().form_invalid(form)


class AdminLoginView(ClinicLoginView):
    def get_success_url(self):
        return self.get_redirect_url() or reverse('admin:index')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                'login_eyebrow': 'Platform administration',
                'login_hero_title': 'ClinicOps Admin',
                'login_hero_text': 'Use the superadmin account to manage platform-wide settings, billing controls, audit tooling, and emergency access actions.',
                'login_heading': 'Superadmin sign in',
                'login_description': 'This sign-in is restricted to ClinicOps platform administrators and requires an authenticator app after password verification.',
                'login_action_url': reverse('admin-login'),
                'show_google_signin': False,
                'show_password_reset_link': False,
                'show_resend_verification_link': False,
            }
        )
        return context

    def form_valid(self, form):
        if not form.get_user().is_superuser:
            form.add_error(None, 'Superadmin access only.')
            return self.form_invalid(form)
        return super().form_valid(form)


class ClinicPasswordChangeView(PasswordChangeView):
    template_name = 'registration/password_change_form.html'
    success_url = reverse_lazy('password_change_done')

    def form_valid(self, form):
        response = super().form_valid(form)
        log_security_event(
            event_type=SecurityEvent.EventType.PASSWORD_CHANGED,
            request=self.request,
            user=self.request.user,
            identifier=self.request.user.email or self.request.user.username,
            metadata={'source': 'password_change'},
        )
        return response


class ClinicPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'registration/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')

    def form_valid(self, form):
        user = getattr(form, 'user', None)
        response = super().form_valid(form)

        if user is not None:
            clinic = _get_user_clinic(user)
            log_security_event(
                event_type=SecurityEvent.EventType.PASSWORD_CHANGED,
                request=self.request,
                user=user,
                identifier=user.email or user.username,
                metadata={'source': 'password_reset'},
            )
            _send_password_reset_notice_email(self.request, user, clinic=clinic)

        return response


def _two_factor_template_context(*, request, title, eyebrow, hero_title, hero_text, panel_title, panel_intro, form=None, extra=None):
    context = {
        'public_brand_name': settings.PUBLIC_BRAND_NAME,
        'public_brand_color': settings.PUBLIC_BRAND_COLOR,
        'public_logo_url': settings.PUBLIC_LOGO_URL,
        'title': title,
        'hero_eyebrow': eyebrow,
        'hero_title': hero_title,
        'hero_text': hero_text,
        'panel_title': panel_title,
        'panel_intro': panel_intro,
        'form': form,
    }
    if extra:
        context.update(extra)
    return context


@login_required
def two_factor_setup_view(request):
    if not user_can_manage_two_factor(request.user):
        return HttpResponseForbidden('This account cannot manage authenticator app security.')

    if user_has_confirmed_two_factor(request.user):
        messages.info(request, 'Two-factor authentication is already enabled for this account.')
        return redirect('settings')

    form = TwoFactorTokenForm(request.POST or None)
    device = get_or_create_setup_device(request.user)
    qr_code_data = build_qr_data_uri(device.config_url)
    secret_key = manual_entry_secret(device)

    if request.method == 'POST' and form.is_valid():
        token = form.cleaned_data['token']
        if device.verify_token(token):
            device.confirmed = True
            device.name = device.name or 'ClinicOps Authenticator'
            device.save(update_fields=['confirmed', 'name'])
            codes = generate_recovery_codes(request.user)
            request.session['two_factor_recovery_codes'] = codes
            finish_two_factor_login(request, device=device)
            request.session.pop(TWO_FACTOR_BACKEND_SESSION_KEY, None)
            log_security_event(
                event_type=SecurityEvent.EventType.TWO_FACTOR_ENABLED,
                request=request,
                user=request.user,
                identifier=request.user.email or request.user.username,
                metadata={
                    'source': 'setup',
                    'recovery_code_count': len(codes),
                },
            )
            messages.success(request, 'Authenticator app enabled. Save the recovery codes before leaving the next screen.')
            return redirect('two-factor-recovery-codes')

        log_security_event(
            event_type=SecurityEvent.EventType.TWO_FACTOR_CHALLENGE_FAILED,
            request=request,
            user=request.user,
            identifier=request.user.email or request.user.username,
            metadata={'source': 'setup'},
        )
        form.add_error('token', 'That authenticator code could not be verified. Try the current 6-digit code again.')

    return render(
        request,
        'registration/two_factor_setup.html',
        _two_factor_template_context(
            request=request,
            title='Set up authenticator',
            eyebrow='Authenticator setup',
            hero_title='Protect this account with an authenticator app.',
            hero_text='Scan the QR code with Google Authenticator, Microsoft Authenticator, Authy, or another TOTP app. Enter the current code once to finish enrollment and generate recovery codes.',
            panel_title='Set up authenticator',
            panel_intro='Enrollment becomes active after you confirm the first code from your app.',
            form=form,
            extra={
                'qr_code_data': qr_code_data,
                'secret_key': secret_key,
                'config_url': device.config_url,
                'is_superuser_setup': request.user.is_superuser,
            },
        ),
    )


@login_required
def two_factor_verify_view(request):
    device = get_confirmed_totp_device(request.user)
    if not device:
        messages.warning(request, 'This account does not have an authenticator app enrolled yet.')
        return redirect('two-factor-setup')

    form = TwoFactorTokenForm(request.POST or None)
    remaining_codes = recovery_code_count(request.user)

    if request.method == 'POST' and form.is_valid():
        token = form.cleaned_data['token']

        if device.verify_token(token):
            finish_two_factor_login(request, device=device)
            request.session.pop(TWO_FACTOR_BACKEND_SESSION_KEY, None)
            log_security_event(
                event_type=SecurityEvent.EventType.TWO_FACTOR_CHALLENGE_PASSED,
                request=request,
                user=request.user,
                identifier=request.user.email or request.user.username,
                metadata={'source': 'totp'},
            )
            return redirect(post_two_factor_redirect(request))

        recovery = consume_recovery_code(request.user, token)
        if recovery:
            recovery.consumed_at = timezone.now()
            recovery.save(update_fields=['consumed_at'])
            finish_two_factor_login(request, device=device)
            request.session.pop(TWO_FACTOR_BACKEND_SESSION_KEY, None)
            log_security_event(
                event_type=SecurityEvent.EventType.TWO_FACTOR_RECOVERY_USED,
                request=request,
                user=request.user,
                identifier=request.user.email or request.user.username,
                metadata={'remaining_recovery_codes': recovery_code_count(request.user)},
            )
            messages.warning(
                request,
                'A recovery code was used for this sign-in. Regenerate your recovery codes after you restore your authenticator app.',
            )
            return redirect(post_two_factor_redirect(request))

        log_security_event(
            event_type=SecurityEvent.EventType.TWO_FACTOR_CHALLENGE_FAILED,
            request=request,
            user=request.user,
            identifier=request.user.email or request.user.username,
            metadata={'source': 'verify'},
        )
        form.add_error('token', 'Enter a valid authenticator code or an unused recovery code.')

    return render(
        request,
        'registration/two_factor_verify.html',
        _two_factor_template_context(
            request=request,
            title='Verify sign-in',
            eyebrow='Two-factor challenge',
            hero_title='Finish signing in with your authenticator app.',
            hero_text='Enter the current 6-digit code from your app. If your phone is unavailable, you can use one of your saved recovery codes instead.',
            panel_title='Verify sign-in',
            panel_intro='This step protects admin and clinic-owner access before the portal opens.',
            form=form,
            extra={'remaining_codes': remaining_codes},
        ),
    )


@login_required
def two_factor_recovery_codes_view(request):
    if not user_can_manage_two_factor(request.user):
        return HttpResponseForbidden('This account cannot manage authenticator app security.')

    codes = request.session.get('two_factor_recovery_codes', [])
    return render(
        request,
        'registration/two_factor_recovery_codes.html',
        _two_factor_template_context(
            request=request,
            title='Recovery codes',
            eyebrow='Recovery access',
            hero_title='Save your recovery codes now.',
            hero_text='Each recovery code works once if you lose your phone or cannot access the authenticator app. Store them offline before you continue.',
            panel_title='Recovery codes',
            panel_intro='These are shown once after setup or regeneration. Generating a new set will invalidate the old ones.',
            extra={
                'recovery_codes': codes,
                'recovery_code_count': recovery_code_count(request.user),
                'can_disable_two_factor': not request.user.is_superuser,
                'continue_url': request.session.get('two_factor_redirect_to') or settings.LOGIN_REDIRECT_URL,
            },
        ),
    )


@login_required
@require_POST
def two_factor_regenerate_view(request):
    if not user_can_manage_two_factor(request.user):
        return HttpResponseForbidden('This account cannot manage authenticator app security.')
    if not user_has_confirmed_two_factor(request.user):
        messages.warning(request, 'Set up an authenticator app before generating recovery codes.')
        return redirect('two-factor-setup')

    codes = generate_recovery_codes(request.user)
    request.session['two_factor_recovery_codes'] = codes
    messages.success(request, 'Recovery codes regenerated. Save the new set now; old codes no longer work.')
    return redirect('two-factor-recovery-codes')


@login_required
@require_POST
def two_factor_disable_view(request):
    if request.user.is_superuser:
        return HttpResponseForbidden('Superadmin 2FA must be reset by another superadmin or through platform administration.')
    if not user_can_manage_two_factor(request.user):
        return HttpResponseForbidden('This account cannot manage authenticator app security.')

    if user_has_confirmed_two_factor(request.user):
        reset_two_factor_for_user(request.user)
        request.session.pop('otp_device_id', None)
        request.session.pop('two_factor_recovery_codes', None)
        log_security_event(
            event_type=SecurityEvent.EventType.TWO_FACTOR_DISABLED,
            request=request,
            user=request.user,
            identifier=request.user.email or request.user.username,
            metadata={'source': 'settings'},
        )
        messages.success(request, 'Authenticator app protection disabled for this account.')
    return redirect('settings')


def page_not_found(request, exception=None):
    return render(request, '404.html', status=404)


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError:
        return fallback


def _normalize_date_range(start_date: date, end_date: date) -> tuple[date, date]:
    if end_date < start_date:
        return end_date, start_date
    return start_date, end_date


def _user_label(user) -> str:
    full_name = user.get_full_name().strip()
    return full_name or user.email or user.username


def _patient_label(patient: Patient) -> str:
    full_name = f'{patient.first_name} {patient.last_name}'.strip()
    return full_name or patient.email or f'Patient {patient.pk}'


def _appointment_time_label(appointment: Appointment) -> str:
    tz = ZoneInfo(appointment.clinic.timezone or 'UTC')
    local_start = timezone.localtime(appointment.start_at, tz)
    return local_start.strftime('%b %d, %Y %I:%M %p')


def _notification_link(path_name: str, *args) -> str:
    return reverse(path_name, args=args) if args else reverse(path_name)


def _clinic_booking_path(clinic: Clinic) -> str:
    """Embed notes: keep the public booking URL consistent anywhere we expose a share or embed link."""
    if clinic.slug:
        return reverse('clinic-booking-slug', args=[clinic.slug])
    return reverse('clinic-booking', args=[clinic.id])


def _clinic_booking_public_url(request, clinic: Clinic) -> str:
    """Embed notes: build the shareable public booking URL from the current host."""
    return request.build_absolute_uri(_clinic_booking_path(clinic))


def _clinic_booking_embed_url(request, clinic: Clinic) -> str:
    """Embed notes: iframe integrations use the same booking page in stripped embed mode."""
    return request.build_absolute_uri(f'{_clinic_booking_path(clinic)}?embed=1')


def _clinic_booking_embed_code(request, clinic: Clinic) -> str:
    """Embed notes: provide a copy-paste iframe snippet for WordPress or custom websites."""
    return (
        '<iframe '
        f'src="{_clinic_booking_embed_url(request, clinic)}" '
        'width="100%" '
        'height="840" '
        'style="border:0;max-width:100%;" '
        'loading="lazy" '
        'referrerpolicy="strict-origin-when-cross-origin" '
        'title="ClinicOps booking">'
        '</iframe>'
    )


def _appointment_duration_minutes(appointment: Appointment) -> int:
    if appointment.appointment_type_id:
        return appointment.appointment_type.duration_minutes
    return max(int((appointment.end_at - appointment.start_at).total_seconds() // 60), 1)


def _appointment_manage_token(appointment: Appointment) -> str:
    payload = {
        'appointment_id': appointment.id,
        'email': (appointment.patient.email or '').strip().lower(),
        'confirmation_code': appointment.confirmation_code,
    }
    return signing.dumps(payload, salt='appointment-manage')


def _appointment_manage_path(appointment: Appointment) -> str:
    return reverse('appointment-manage', args=[_appointment_manage_token(appointment)])


def _appointment_manage_url(request, appointment: Appointment) -> str:
    return request.build_absolute_uri(_appointment_manage_path(appointment))


def _appointment_lookup_url(request) -> str:
    return request.build_absolute_uri(reverse('appointment-lookup'))


def _resolve_manage_appointment(token: str) -> tuple[Appointment | None, str | None]:
    max_age = getattr(settings, 'APPOINTMENT_MANAGE_LINK_MAX_AGE', 60 * 60 * 24 * 60)
    try:
        payload = signing.loads(token, salt='appointment-manage', max_age=max_age)
    except signing.SignatureExpired:
        return None, 'This self-service appointment link has expired. Use appointment lookup with your email and confirmation code instead.'
    except signing.BadSignature:
        return None, 'This self-service appointment link is invalid. Use appointment lookup with your email and confirmation code instead.'

    appointment = (
        Appointment.objects.filter(pk=payload.get('appointment_id'))
        .select_related('clinic', 'staff', 'patient', 'appointment_type')
        .first()
    )
    if not appointment:
        return None, 'This appointment could not be found anymore.'

    patient_email = (appointment.patient.email or '').strip().lower()
    if patient_email != payload.get('email') or appointment.confirmation_code != payload.get('confirmation_code'):
        return None, 'This appointment link no longer matches the booking record.'

    return appointment, None


def _appointment_email_context(request, appointment: Appointment, *, headline: str, detail_copy: str = '') -> dict:
    clinic_tz = ZoneInfo(appointment.clinic.timezone or 'UTC')
    start_at_local = timezone.localtime(appointment.start_at, clinic_tz)
    end_at_local = timezone.localtime(appointment.end_at, clinic_tz)
    return {
        'appointment': appointment,
        'clinic': appointment.clinic,
        'patient': appointment.patient,
        'staff': appointment.staff,
        'appointment_type': appointment.appointment_type,
        'headline': headline,
        'detail_copy': detail_copy,
        'start_at_local': start_at_local,
        'end_at_local': end_at_local,
        'manage_url': _appointment_manage_url(request, appointment),
        'lookup_url': _appointment_lookup_url(request),
    }


def _send_appointment_email(
    request,
    appointment: Appointment,
    *,
    subject_label: str,
    headline: str,
    detail_copy: str = '',
) -> bool:
    if not getattr(settings, 'SEND_BOOKING_CONFIRMATION', True) or not appointment.patient.email:
        return False
    context = _appointment_email_context(
        request,
        appointment,
        headline=headline,
        detail_copy=detail_copy,
    )
    context['subject_label'] = subject_label
    return _send_rendered_email(
        subject_template='core/appointment_confirmation_subject.txt',
        text_template='core/appointment_confirmation.txt',
        html_template='core/appointment_confirmation.html',
        context=context,
        recipients=[appointment.patient.email],
    )


def _appointment_notification_recipients(appointment: Appointment):
    recipients = list(
        User.objects.filter(
            staff__clinic=appointment.clinic,
            staff__is_active=True,
            is_active=True,
            groups__name__in=['Admin', 'FrontDesk'],
        )
        .distinct()
        .order_by('id')
    )
    if appointment.staff_id and appointment.staff.user_id and appointment.staff.user.is_active:
        recipients.append(appointment.staff.user)
    return recipients


def _notify_clinic_appointment_created(*, appointment: Appointment, actor=None, event_type=None, title='Appointment added'):
    service_name = appointment.appointment_type.name if appointment.appointment_type else 'General appointment'
    create_clinic_notifications(
        appointment.clinic,
        actor=actor,
        recipients=_appointment_notification_recipients(appointment),
        event_type=event_type or Notification.EventType.APPOINTMENT_CREATED,
        level=Notification.Level.SUCCESS,
        title=title,
        body=(
            f'{_patient_label(appointment.patient)} is scheduled with {_user_label(appointment.staff.user)} '
            f'on {_appointment_time_label(appointment)} for {service_name}.'
        ),
        link=_notification_link('staff-appointment-edit', appointment.id),
        metadata={
            'appointment_id': appointment.id,
            'patient_id': appointment.patient_id,
            'staff_id': appointment.staff_id,
        },
    )


def _notify_clinic_appointment_updated(*, appointment: Appointment, actor=None):
    service_name = appointment.appointment_type.name if appointment.appointment_type else 'General appointment'
    create_clinic_notifications(
        appointment.clinic,
        actor=actor,
        recipients=_appointment_notification_recipients(appointment),
        event_type=Notification.EventType.APPOINTMENT_UPDATED,
        level=Notification.Level.INFO,
        title='Appointment updated',
        body=(
            f'{_patient_label(appointment.patient)} now has {service_name} with '
            f'{_user_label(appointment.staff.user)} on {_appointment_time_label(appointment)}.'
        ),
        link=_notification_link('staff-appointment-edit', appointment.id),
        metadata={'appointment_id': appointment.id},
    )


def _notify_clinic_appointment_cancelled(*, appointment: Appointment, actor=None):
    service_name = appointment.appointment_type.name if appointment.appointment_type else 'General appointment'
    create_clinic_notifications(
        appointment.clinic,
        actor=actor,
        recipients=_appointment_notification_recipients(appointment),
        event_type=Notification.EventType.APPOINTMENT_UPDATED,
        level=Notification.Level.WARNING,
        title='Appointment cancelled',
        body=(
            f'{_patient_label(appointment.patient)} cancelled {service_name} with '
            f'{_user_label(appointment.staff.user)} that was scheduled for {_appointment_time_label(appointment)}.'
        ),
        link=_notification_link('staff-appointment-edit', appointment.id),
        metadata={'appointment_id': appointment.id},
    )


def _notify_clinic_staff_change(*, clinic: Clinic, member: Staff, actor=None, created=False):
    create_clinic_notifications(
        clinic,
        actor=actor,
        admins_only=True,
        event_type=Notification.EventType.STAFF_ADDED if created else Notification.EventType.STAFF_UPDATED,
        level=Notification.Level.SUCCESS if created else Notification.Level.INFO,
        title='Staff added' if created else 'Staff updated',
        body=f'{_user_label(member.user)} was {"added to" if created else "updated in"} the clinic staff roster.',
        link=_notification_link('staff-member-edit', member.id),
        metadata={'staff_id': member.id, 'user_id': member.user_id},
    )


def _notify_clinic_service_change(*, clinic: Clinic, appointment_type: AppointmentType, actor=None, created=False):
    create_clinic_notifications(
        clinic,
        actor=actor,
        admins_only=True,
        event_type=Notification.EventType.SERVICE_ADDED if created else Notification.EventType.SERVICE_UPDATED,
        level=Notification.Level.SUCCESS if created else Notification.Level.INFO,
        title='Service added' if created else 'Service updated',
        body=(
            f'{appointment_type.name} is now configured for {appointment_type.duration_minutes} minutes'
            + (
                f' at ${appointment_type.price_cents / 100:.2f}.'
                if appointment_type.price_cents is not None
                else '.'
            )
        ),
        link=_notification_link('appointment-type-edit', appointment_type.id),
        metadata={'appointment_type_id': appointment_type.id},
    )


def _notify_clinic_patient_signup(*, clinic: Clinic, patient: Patient):
    create_clinic_notifications(
        clinic,
        role_names=['Admin', 'FrontDesk'],
        event_type=Notification.EventType.PATIENT_SIGNED_UP,
        level=Notification.Level.SUCCESS,
        title='New patient signup',
        body=f'{_patient_label(patient)} created a patient account for {clinic.name}.',
        link=_notification_link('staff-patient-edit', patient.id),
        metadata={'patient_id': patient.id},
    )


def _notify_clinic_waitlist_entry(*, waitlist_entry: WaitlistEntry):
    service_name = waitlist_entry.appointment_type.name if waitlist_entry.appointment_type else 'General booking'
    create_clinic_notifications(
        waitlist_entry.clinic,
        role_names=['Admin', 'FrontDesk'],
        event_type=Notification.EventType.GENERIC,
        level=Notification.Level.INFO,
        title='New waitlist request',
        body=(
            f'{waitlist_entry.first_name} {waitlist_entry.last_name} joined the waitlist for {service_name}.'
        ),
        link=_notification_link('staff-waitlist'),
        metadata={'waitlist_entry_id': waitlist_entry.id},
    )


def _subscription_notification_level(status: str) -> str:
    if status == ClinicSubscription.Status.ACTIVE:
        return Notification.Level.SUCCESS
    if status in {ClinicSubscription.Status.SUSPENDED, ClinicSubscription.Status.CANCELLED, ClinicSubscription.Status.EXPIRED}:
        return Notification.Level.WARNING
    return Notification.Level.INFO


def _notify_clinic_subscription_change(
    *,
    clinic: Clinic,
    subscription: ClinicSubscription,
    actor=None,
    title='Subscription updated',
    created=False,
):
    plan_name = subscription.plan.name if subscription.plan_id else 'Clinic plan'
    status_label = subscription.get_status_display().lower()
    create_clinic_notifications(
        clinic,
        actor=actor,
        admins_only=True,
        event_type=(
            Notification.EventType.SUBSCRIPTION_ACTIVATED
            if created or subscription.status == ClinicSubscription.Status.ACTIVE
            else Notification.EventType.SUBSCRIPTION_UPDATED
        ),
        level=_subscription_notification_level(subscription.status),
        title=title,
        body=f'{plan_name} is currently {status_label} for {clinic.name}.',
        link=_notification_link('billing'),
        metadata={'subscription_id': subscription.id, 'status': subscription.status},
    )


def _resolve_notification_destination(notification: Notification, user):
    metadata = notification.metadata or {}
    try:
        staff_profile = user.staff
    except Staff.DoesNotExist:
        staff_profile = None

    appointment_id = metadata.get('appointment_id')
    if appointment_id and staff_profile:
        appointment = (
            Appointment.objects.filter(pk=appointment_id, clinic=staff_profile.clinic)
            .select_related('staff')
            .first()
        )
        if appointment:
            if _is_admin(user):
                return reverse('staff-appointment-edit', args=[appointment.id]), ''
            if _is_frontdesk(user):
                if appointment.status == Appointment.Status.COMPLETED:
                    return reverse('staff-appointment-history', args=[appointment.id]), ''
                return reverse('staff-appointment-edit', args=[appointment.id]), ''
            if _is_doctor(user) and appointment.staff_id == staff_profile.id:
                return reverse('staff-appointment-edit', args=[appointment.id]), ''
            return reverse('staff-appointments'), ''

    patient_id = metadata.get('patient_id')
    if patient_id and staff_profile:
        patient = Patient.objects.filter(pk=patient_id, clinic=staff_profile.clinic).first()
        if patient:
            if _is_admin(user) or _is_frontdesk(user):
                return reverse('staff-patient-edit', args=[patient.id]), ''
            if _is_doctor(user):
                has_relationship = Appointment.objects.filter(
                    clinic=staff_profile.clinic,
                    staff=staff_profile,
                    patient=patient,
                ).exists()
                if has_relationship:
                    return reverse('staff-patient-edit', args=[patient.id]), ''
            return reverse('staff-patients'), ''

    staff_id = metadata.get('staff_id')
    if staff_id and staff_profile:
        if _is_admin(user):
            member = Staff.objects.filter(pk=staff_id, clinic=staff_profile.clinic).first()
            if member:
                return reverse('staff-member-edit', args=[member.id]), ''
        return reverse('notifications'), 'This staff update is only available to clinic admins.'

    appointment_type_id = metadata.get('appointment_type_id')
    if appointment_type_id and staff_profile:
        if _is_admin(user):
            appointment_type = AppointmentType.objects.filter(
                pk=appointment_type_id,
                clinic=staff_profile.clinic,
            ).first()
            if appointment_type:
                return reverse('appointment-type-edit', args=[appointment_type.id]), ''
        return reverse('notifications'), 'This service update is only available to clinic admins.'

    subscription_id = metadata.get('subscription_id')
    if subscription_id and staff_profile:
        if _is_admin(user):
            subscription = ClinicSubscription.objects.filter(
                pk=subscription_id,
                clinic=staff_profile.clinic,
            ).first()
            if subscription:
                return reverse('billing'), ''
        return reverse('notifications'), 'Billing updates are only available to clinic admins.'

    return '', ''


def _apply_subscription_state(
    subscription: ClinicSubscription,
    *,
    plan=_UNSET,
    raw_status=_UNSET,
    started_at=_UNSET,
    current_period_end=_UNSET,
    last_event_type=_UNSET,
):
    update_fields = []

    if plan is not _UNSET and plan is not None and subscription.plan_id != plan.id:
        subscription.plan = plan
        update_fields.append('plan')

    if raw_status is not _UNSET and raw_status:
        mapped_status = map_paypal_status(raw_status)
        if subscription.status != mapped_status:
            subscription.status = mapped_status
            update_fields.append('status')

    if started_at is not _UNSET and subscription.started_at != started_at:
        subscription.started_at = started_at
        update_fields.append('started_at')

    if current_period_end is not _UNSET and subscription.current_period_end != current_period_end:
        subscription.current_period_end = current_period_end
        update_fields.append('current_period_end')

    if last_event_type is not _UNSET and subscription.last_event_type != last_event_type:
        subscription.last_event_type = last_event_type
        update_fields.append('last_event_type')

    if update_fields:
        subscription.save(update_fields=update_fields)

    return subscription


def _paypal_event_id(event: dict, raw_body: bytes) -> str:
    return event.get('id') or f"raw-{hashlib.sha256(raw_body).hexdigest()}"


def _clinic_id_from_custom_id(custom_id: str | None) -> int | None:
    if not custom_id or not custom_id.startswith('clinic-'):
        return None
    try:
        return int(custom_id.split('-', 1)[1])
    except (IndexError, ValueError):
        return None


def _upsert_pending_subscription(
    *,
    clinic: Clinic,
    plan: Plan,
    subscription_id: str,
    last_event_type: str,
):
    subscription, _ = ClinicSubscription.objects.update_or_create(
        paypal_subscription_id=subscription_id,
        defaults={
            'clinic': clinic,
            'plan': plan,
            'status': ClinicSubscription.Status.PENDING,
            'last_event_type': last_event_type,
        },
    )
    return subscription


def _local_subscription_id(*, clinic: Clinic, plan: Plan) -> str:
    """Plan notes: Free plans use a local synthetic ID so they can bypass PayPal safely."""
    return f'LOCAL-{clinic.id}-{plan.id}'


def _activate_local_subscription(*, clinic: Clinic, plan: Plan, last_event_type: str):
    """Plan notes: centralize local Free activation so signup and billing use the same path."""
    local_subscription_id = _local_subscription_id(clinic=clinic, plan=plan)
    subscription, _ = ClinicSubscription.objects.update_or_create(
        paypal_subscription_id=local_subscription_id,
        defaults={
            'clinic': clinic,
            'plan': plan,
            'status': ClinicSubscription.Status.ACTIVE,
            'started_at': timezone.now(),
            'current_period_end': None,
            'cancel_at_period_end': False,
            'last_event_type': last_event_type,
        },
    )
    ClinicSubscription.objects.filter(
        clinic=clinic,
        paypal_subscription_id__startswith='LOCAL-',
        status=ClinicSubscription.Status.ACTIVE,
    ).exclude(pk=subscription.pk).update(
        status=ClinicSubscription.Status.CANCELLED,
        last_event_type='LOCAL_REPLACED',
    )
    return subscription


def _activate_selected_plan(
    *,
    clinic: Clinic,
    plan: Plan,
    subscription_id: str | None,
    last_event_type: str,
    sync_event_type: str,
):
    """Plan notes: Free plans activate locally, while paid plans stay on the existing PayPal flow."""
    if plan.is_free:
        return _activate_local_subscription(
            clinic=clinic,
            plan=plan,
            last_event_type=last_event_type,
        )

    subscription = _upsert_pending_subscription(
        clinic=clinic,
        plan=plan,
        subscription_id=subscription_id or '',
        last_event_type=last_event_type,
    )
    try:
        _sync_subscription_from_paypal(subscription, last_event_type=sync_event_type)
    except PayPalError:
        logger.warning(
            'PayPal subscription sync failed during client activation for %s',
            subscription_id,
        )
    return subscription


def _limit_reached_message(*, item: dict, resource_label: str, action_label: str) -> str:
    """Plan notes: keep Free-plan upgrade copy consistent wherever a quota blocks an action."""
    return (
        f'Free plan limit reached for {resource_label}: {item["summary_label"]}. '
        f'Upgrade in billing to continue {action_label}.'
    )


def _searchable_appointments_for_staff(staff: Staff):
    """Portal search notes: keep appointment search scoped to the current clinic and role."""
    qs = Appointment.objects.filter(clinic=staff.clinic).select_related('patient', 'staff__user', 'appointment_type')
    if _is_doctor(staff.user) and not _is_admin(staff.user):
        qs = qs.filter(staff=staff)
    return qs


def _searchable_patients_for_staff(staff: Staff):
    """Portal search notes: keep patient search aligned with existing patient-list permissions."""
    qs = Patient.objects.filter(clinic=staff.clinic)
    if _is_doctor(staff.user) and not _is_admin(staff.user):
        patient_ids = Appointment.objects.filter(
            clinic=staff.clinic,
            staff=staff,
        ).values_list('patient_id', flat=True)
        qs = qs.filter(id__in=patient_ids)
    return qs


def _matches_patient_search(patient: Patient, query: str) -> bool:
    """Portal search notes: encrypted patient fields need application-side matching."""
    normalized = query.lower()
    full_name = f'{patient.first_name} {patient.last_name}'.strip().lower()
    return any(
        normalized in value
        for value in [
            (patient.first_name or '').lower(),
            (patient.last_name or '').lower(),
            full_name,
            (patient.email or '').lower(),
            (patient.phone or '').lower(),
        ]
        if value
    )


def _matches_appointment_search(appointment: Appointment, query: str) -> bool:
    """Portal search notes: confirmation codes stay exact-friendly while patient fields match in Python."""
    normalized = query.lower()
    service_name = (appointment.appointment_type.name if appointment.appointment_type else '').lower()
    full_name = f'{appointment.patient.first_name} {appointment.patient.last_name}'.strip().lower()
    return any(
        normalized in value
        for value in [
            (appointment.confirmation_code or '').lower(),
            (appointment.patient.first_name or '').lower(),
            (appointment.patient.last_name or '').lower(),
            full_name,
            (appointment.patient.email or '').lower(),
            (appointment.patient.phone or '').lower(),
            service_name,
        ]
        if value
    )


def _collect_search_matches(queryset, matcher, query: str, *, limit: int, candidate_limit: int | None = None):
    """Portal search notes: stop scanning once enough matches are found to keep preview requests lightweight."""
    matches = []
    scanned = 0
    for item in queryset.iterator(chunk_size=100):
        scanned += 1
        if matcher(item, query):
            matches.append(item)
            if len(matches) >= limit:
                break
        if candidate_limit is not None and scanned >= candidate_limit:
            break
    return matches


def _perform_portal_search(
    *,
    staff: Staff,
    query: str,
    appointment_limit: int,
    patient_limit: int,
    candidate_limit: int | None = None,
):
    """Portal search notes: share the same scoped matching rules between the page results and the live preview."""
    normalized_query = (query or '').strip()
    if not normalized_query:
        return None, [], []

    normalized_code = normalized_query.replace(' ', '').upper()
    exact_appointment = _searchable_appointments_for_staff(staff).filter(
        confirmation_code__iexact=normalized_code
    ).first()
    if exact_appointment:
        return exact_appointment, [exact_appointment], []

    appointments = _collect_search_matches(
        _searchable_appointments_for_staff(staff).order_by('-start_at'),
        _matches_appointment_search,
        normalized_query,
        limit=appointment_limit,
        candidate_limit=candidate_limit,
    )
    patients = _collect_search_matches(
        _searchable_patients_for_staff(staff).order_by('-id'),
        _matches_patient_search,
        normalized_query,
        limit=patient_limit,
        candidate_limit=candidate_limit,
    )
    return None, appointments, patients


def _sync_subscription_from_paypal(subscription: ClinicSubscription, *, last_event_type: str):
    details = get_subscription(subscription.paypal_subscription_id)
    billing_info = details.get('billing_info') or {}
    _apply_subscription_state(
        subscription,
        raw_status=details.get('status') or _UNSET,
        started_at=parse_paypal_datetime(details.get('start_time')) or _UNSET,
        current_period_end=parse_paypal_datetime(billing_info.get('next_billing_time')),
        last_event_type=last_event_type,
    )
    return details


def _finalize_paypal_event(
    webhook_event: PayPalWebhookEvent,
    *,
    status: str,
    summary: str = '',
    error_message: str = '',
    subscription: ClinicSubscription | None = None,
):
    webhook_event.status = status
    webhook_event.summary = summary
    webhook_event.error_message = error_message
    webhook_event.processed_at = timezone.now()
    webhook_event.clinic_subscription = subscription
    update_fields = ['status', 'summary', 'error_message', 'processed_at', 'clinic_subscription']
    webhook_event.save(update_fields=update_fields)


def _build_paypal_webhook_event(event: dict, raw_body: bytes, *, subscription_id: str | None):
    resource = event.get('resource', {}) or {}
    return PayPalWebhookEvent.objects.get_or_create(
        event_id=_paypal_event_id(event, raw_body),
        defaults={
            'event_type': event.get('event_type') or '',
            'resource_type': resource.get('resource_type') or '',
            'resource_id': subscription_id or resource.get('id') or '',
            'summary': event.get('summary') or '',
            'status': PayPalWebhookEvent.ProcessingStatus.RECEIVED,
            'payload': event,
        },
    )


def _build_walk_in_form(*, clinic: Clinic, data=None, prefix: str | None = None):
    staff_qs = Staff.objects.filter(clinic=clinic, is_active=True).select_related('user')
    appointment_type_qs = AppointmentType.objects.filter(clinic=clinic, is_active=True)
    form_kwargs = {
        'staff_qs': staff_qs,
        'appointment_type_qs': appointment_type_qs,
    }
    if prefix:
        form_kwargs['prefix'] = prefix
    if data is not None:
        return WalkInAppointmentForm(data, **form_kwargs)
    return WalkInAppointmentForm(**form_kwargs)


def _save_walk_in_appointment(form, clinic: Clinic, tz: ZoneInfo):
    start_at = form.cleaned_data['start_at']
    if timezone.is_naive(start_at):
        start_at = timezone.make_aware(start_at, tz)
    staff_selected = form.cleaned_data['staff']
    appointment_type = form.cleaned_data.get('appointment_type')
    duration_minutes = (
        appointment_type.duration_minutes
        if appointment_type
        else getattr(settings, 'APPOINTMENT_SLOT_MINUTES', 30)
    )
    end_at = start_at + timedelta(minutes=duration_minutes)

    email = form.cleaned_data['email'].strip().lower()
    patient = Patient.objects.filter(clinic=clinic, email=email).first()
    if not patient:
        patient = Patient.objects.create(
            clinic=clinic,
            first_name=form.cleaned_data['first_name'],
            last_name=form.cleaned_data['last_name'],
            email=email,
            phone=form.cleaned_data['phone'],
            dob=form.cleaned_data.get('dob'),
        )

    appointment = Appointment(
        clinic=clinic,
        appointment_type=appointment_type,
        staff=staff_selected,
        patient=patient,
        start_at=start_at,
        end_at=end_at,
        notes=form.cleaned_data.get('notes'),
    )
    try:
        appointment.save()
    except ValidationError:
        form.add_error('start_at', 'That time overlaps another appointment for this staff.')
        return None
    return appointment


def _staff_member_initial(user) -> dict:
    return {
        'email': user.email or user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'role': _staff_role_for_user(user) or 'Doctor',
        'is_active': user.is_active,
    }


def _save_staff_member_form(request, clinic: Clinic, form, *, member: Staff | None = None):
    email = form.cleaned_data['email']
    is_create = member is None
    user = member.user if member is not None else None
    was_active = user.is_active if user is not None else False

    if is_create:
        if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
            form.add_error('email', 'An account with this email already exists.')
            return None
        is_active = bool(form.cleaned_data.get('is_active'))
        user = User.objects.create_user(
            username=email,
            email=email,
            first_name=form.cleaned_data.get('first_name') or '',
            last_name=form.cleaned_data.get('last_name') or '',
            password=form.cleaned_data['password'],
            is_staff=True,
            is_active=is_active,
        )
        member = Staff.objects.create(
            user=user,
            clinic=clinic,
            is_active=is_active,
        )
    else:
        if email.lower() != (user.email or user.username).lower():
            if (
                User.objects.filter(username__iexact=email).exclude(pk=user.pk).exists()
                or User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists()
            ):
                form.add_error('email', 'An account with this email already exists.')
                return None

        user.username = email
        user.email = email
        user.first_name = form.cleaned_data.get('first_name') or ''
        user.last_name = form.cleaned_data.get('last_name') or ''
        user.is_active = bool(form.cleaned_data.get('is_active'))
        user.is_staff = True
        new_password = form.cleaned_data.get('password')
        if new_password:
            user.set_password(new_password)
        user.save()

        member.is_active = user.is_active
        member.save(update_fields=['is_active'])

    staff_groups = Group.objects.filter(name__in=ALLOWED_GROUPS)
    user.groups.remove(*staff_groups)
    role = form.cleaned_data['role']
    group = Group.objects.filter(name=role).first()
    if group:
        user.groups.add(group)

    if not user.is_active:
        _send_verification_email(request, user, clinic=clinic)
    elif is_create or not was_active:
        _send_staff_welcome_email(request, user, clinic=clinic)

    return member


def _build_staff_members_context(clinic: Clinic) -> dict:
    members = list(
        Staff.objects.filter(clinic=clinic)
        .select_related('user')
        .order_by('user__last_name', 'user__first_name')
    )
    tz = ZoneInfo(clinic.timezone or 'UTC')
    now = timezone.now()
    staff_ids = [member.id for member in members]
    appointments_qs = Appointment.objects.none()
    if staff_ids:
        appointments_qs = (
            Appointment.objects.filter(clinic=clinic, staff_id__in=staff_ids)
            .only('staff_id', 'start_at')
            .order_by('start_at')
        )
    counts_by_staff = {
        member.id: {'appointment_count': 0, 'upcoming_count': 0, 'next_appointment': None}
        for member in members
    }
    for appt in appointments_qs:
        row = counts_by_staff.get(appt.staff_id)
        if not row:
            continue
        row['appointment_count'] += 1
        if appt.start_at >= now:
            row['upcoming_count'] += 1
            if row['next_appointment'] is None:
                row['next_appointment'] = appt

    staff_rows = []
    active_count = 0
    inactive_count = 0
    role_counts = {}
    for member in members:
        role = _staff_role_for_user(member.user) or '-'
        role_counts[role] = role_counts.get(role, 0) + 1
        is_active = member.is_active and member.user.is_active
        if is_active:
            active_count += 1
        else:
            inactive_count += 1
        summary = counts_by_staff.get(member.id, {})
        next_appointment = summary.get('next_appointment')
        staff_rows.append(
            {
                'staff': member,
                'role': role,
                'is_active': is_active,
                'appointment_count': summary.get('appointment_count', 0),
                'upcoming_count': summary.get('upcoming_count', 0),
                'next_appointment_local': timezone.localtime(next_appointment.start_at, tz) if next_appointment else None,
            }
        )

    return {
        'staff_rows': staff_rows,
        'total_staff_count': len(staff_rows),
        'active_staff_count': active_count,
        'inactive_staff_count': inactive_count,
        'role_counts': sorted(role_counts.items()),
        'current_local_time': timezone.localtime(now, tz),
    }


def _filter_appointments(
    clinic: Clinic,
    start_dt: datetime,
    end_dt: datetime,
    staff_id: str | None,
    status: str | None,
):
    qs = Appointment.objects.filter(
        clinic=clinic,
        start_at__gte=start_dt,
        start_at__lte=end_dt,
    )
    if staff_id:
        try:
            staff_id_int = int(staff_id)
        except (TypeError, ValueError):
            staff_id_int = None
        if staff_id_int:
            qs = qs.filter(staff_id=staff_id_int)
    if status and status in dict(Appointment.Status.choices):
        qs = qs.filter(status=status)
    return qs


def _send_verification_email(request, user, clinic=None):
    if clinic is None:
        clinic = _get_user_clinic(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    verify_url = request.build_absolute_uri(
        reverse('verify-email', args=[uid, token])
    )
    clinic_name = clinic.name if clinic else 'ClinicOps'
    context = {
        'user': user,
        'clinic': clinic,
        'clinic_name': clinic_name,
        'verify_url': verify_url,
    }
    _send_rendered_email(
        subject_template='core/email_verify_subject.txt',
        text_template='core/email_verify.txt',
        html_template='core/email_verify.html',
        context=context,
        recipients=[user.email],
    )


def _send_rendered_email(*, subject_template: str, text_template: str, html_template: str | None, context: dict, recipients: list[str]) -> bool:
    subject = render_to_string(subject_template, context).strip()
    message = render_to_string(text_template, context)
    email = EmailMultiAlternatives(
        subject=subject,
        body=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    if html_template:
        email.attach_alternative(render_to_string(html_template, context), 'text/html')

    try:
        email.send(fail_silently=False)
        return True
    except Exception:
        logger.exception(
            'Transactional email send failed for template=%s recipients=%s',
            subject_template,
            recipients,
        )
        return False


def _send_staff_welcome_email(request, user, clinic=None):
    if clinic is None:
        clinic = _get_user_clinic(user)
    clinic_name = clinic.name if clinic else 'ClinicOps'
    context = {
        'user': user,
        'clinic': clinic,
        'clinic_name': clinic_name,
        'login_url': request.build_absolute_uri(reverse('login')),
    }
    _send_rendered_email(
        subject_template='core/staff_welcome_subject.txt',
        text_template='core/staff_welcome.txt',
        html_template='core/staff_welcome.html',
        context=context,
        recipients=[user.email],
    )


def _send_password_reset_notice_email(request, user, clinic=None):
    if not user.email:
        return False

    if clinic is None:
        clinic = _get_user_clinic(user)

    clinic_name = clinic.name if clinic else 'ClinicOps'
    context = {
        'user': user,
        'clinic': clinic,
        'clinic_name': clinic_name,
        'login_url': request.build_absolute_uri(reverse('login')),
    }
    return _send_rendered_email(
        subject_template='core/password_reset_notice_subject.txt',
        text_template='core/password_reset_notice.txt',
        html_template='core/password_reset_notice.html',
        context=context,
        recipients=[user.email],
    )


def _platform_alert_recipients():
    if settings.PLATFORM_ALERT_EMAILS:
        return settings.PLATFORM_ALERT_EMAILS
    try:
        return list(
            User.objects.filter(is_superuser=True, is_active=True)
            .exclude(email='')
            .values_list('email', flat=True)
        )
    except DatabaseError:
        logger.warning(
            'ClinicOps platform alert recipients could not be loaded because the database is unavailable.',
            exc_info=True,
        )
        return []


def _user_display_name(user):
    full_name = user.get_full_name().strip()
    return full_name or user.email or user.username


def _send_platform_clinic_signup_alert(*, request, clinic: Clinic, owner_user):
    recipients = _platform_alert_recipients()
    if not recipients:
        return False

    context = {
        'clinic': clinic,
        'owner_user': owner_user,
        'owner_name': _user_display_name(owner_user),
        'owner_email': owner_user.email,
        'login_url': request.build_absolute_uri(reverse('login')),
        'plan_mode': 'Pending selection',
    }
    return _send_rendered_email(
        subject_template='core/platform_clinic_signup_alert_subject.txt',
        text_template='core/platform_clinic_signup_alert.txt',
        html_template='core/platform_clinic_signup_alert.html',
        context=context,
        recipients=recipients,
    )


def _send_platform_plan_activation_alert(*, request, clinic: Clinic, owner_user, plan: Plan, subscription: ClinicSubscription):
    recipients = _platform_alert_recipients()
    if not recipients:
        return False

    context = {
        'clinic': clinic,
        'owner_user': owner_user,
        'owner_name': _user_display_name(owner_user),
        'owner_email': owner_user.email,
        'plan': plan,
        'plan_mode': 'Free' if plan.is_free else 'Premium',
        'subscription': subscription,
        'status_label': subscription.get_status_display(),
        'billing_url': request.build_absolute_uri(reverse('billing')) if request.user.is_authenticated else '',
    }
    return _send_rendered_email(
        subject_template='core/platform_plan_activation_alert_subject.txt',
        text_template='core/platform_plan_activation_alert.txt',
        html_template='core/platform_plan_activation_alert.html',
        context=context,
        recipients=recipients,
    )


def _clinic_message_alert_recipients(clinic: Clinic) -> list[str]:
    recipients = []
    owner_user = clinic_owner_user(clinic)
    if owner_user and owner_user.email:
        recipients.append(owner_user.email)
    if clinic.email:
        recipients.append(clinic.email)
    # Messaging alerts are clinic-facing, so keep the recipient list tight and deduplicated.
    return list(dict.fromkeys(recipients))


def _thread_url_for_patient(request, thread: MessageThread) -> str:
    if thread.patient.user_id:
        return request.build_absolute_uri(reverse('messages-thread', args=[thread.id]))
    if thread.appointment_id:
        return _appointment_manage_url(request, thread.appointment)
    return request.build_absolute_uri(reverse('clinic-booking-slug', args=[thread.clinic.slug]))


def _send_clinic_message_alert(*, request, thread: MessageThread, message):
    recipients = _clinic_message_alert_recipients(thread.clinic)
    if not recipients:
        return False
    context = {
        'thread': thread,
        'message': message,
        'clinic': thread.clinic,
        'patient': thread.patient,
        'patient_name': patient_display_name(thread.patient),
        'inbox_url': request.build_absolute_uri(reverse('messages-thread', args=[thread.id])),
    }
    return _send_rendered_email(
        subject_template='core/clinic_message_alert_subject.txt',
        text_template='core/clinic_message_alert.txt',
        html_template='core/clinic_message_alert.html',
        context=context,
        recipients=recipients,
    )


def _send_patient_message_notice(*, request, thread: MessageThread, message):
    if not thread.patient.email:
        return False
    context = {
        'thread': thread,
        'message': message,
        'clinic': thread.clinic,
        'patient': thread.patient,
        'message_url': _thread_url_for_patient(request, thread),
    }
    return _send_rendered_email(
        subject_template='core/patient_message_notice_subject.txt',
        text_template='core/patient_message_notice.txt',
        html_template='core/patient_message_notice.html',
        context=context,
        recipients=[thread.patient.email],
    )


def _get_user_clinic(user):
    try:
        return user.staff.clinic
    except Staff.DoesNotExist:
        pass
    patient = Patient.objects.filter(user=user).select_related('clinic').first()
    if patient:
        return patient.clinic
    return None


def _get_clinic_owner_user(clinic: Clinic):
    owner_user = clinic_owner_user(clinic)
    if owner_user:
        return owner_user
    fallback_staff = Staff.objects.filter(clinic=clinic).select_related('user').order_by('id').first()
    return fallback_staff.user if fallback_staff else None


def _get_active_patient_profile(request):
    profiles = Patient.objects.filter(user=request.user).select_related('clinic')
    if not profiles.exists():
        return None, profiles

    selected_id = request.session.get('patient_clinic_id')
    if selected_id:
        patient = profiles.filter(clinic_id=selected_id).first()
        if patient:
            return patient, profiles

    if profiles.count() == 1:
        return profiles.first(), profiles

    return None, profiles


def _is_admin(user):
    return user.is_superuser or user.groups.filter(name='Admin').exists()


def _is_doctor(user):
    return user.groups.filter(name='Doctor').exists()


def _is_nurse(user):
    return user.groups.filter(name='Nurse').exists()


def _is_frontdesk(user):
    return user.groups.filter(name='FrontDesk').exists()


def _staff_role_for_user(user):
    return (
        user.groups.filter(name__in=ALLOWED_GROUPS)
        .values_list('name', flat=True)
        .first()
    )


def _require_staff_portal(request):
    if not (request.user.is_superuser or request.user.groups.filter(name__in=ALLOWED_GROUPS).exists()):
        return None, HttpResponseForbidden('Role access required.')
    try:
        staff = request.user.staff
    except Staff.DoesNotExist:
        return None, HttpResponseForbidden('Staff access required.')
    return staff, None


def _build_schedule_summary(appointments, tz, start_date=None, end_date=None):
    now = timezone.now()
    today = timezone.localdate(now, tz)
    total_count = len(appointments)
    completed_count = 0
    cancelled_count = 0
    upcoming_count = 0
    today_count = 0
    unique_patient_ids = set()
    active_staff_ids = set()
    next_appointment = None
    appointment_rows = []
    grouped_map = {}

    for appt in appointments:
        unique_patient_ids.add(appt.patient_id)
        active_staff_ids.add(appt.staff_id)

        if appt.status == Appointment.Status.COMPLETED:
            completed_count += 1
        elif appt.status == Appointment.Status.CANCELLED:
            cancelled_count += 1

        if appt.start_at >= now:
            upcoming_count += 1
            if next_appointment is None:
                next_appointment = appt

        local_start = timezone.localtime(appt.start_at, tz)
        local_end = timezone.localtime(appt.end_at, tz)
        row = {
            'appointment': appt,
            'local_start': local_start,
            'local_end': local_end,
        }
        appointment_rows.append(row)

        local_day = local_start.date()
        grouped_map.setdefault(local_day, []).append(row)
        if local_day == today:
            today_count += 1

    scheduled_count = total_count - completed_count - cancelled_count
    date_rows = []
    average_daily_load = 0
    busiest_day_label = ''
    busiest_day_count = 0

    if start_date and end_date:
        cursor = start_date
        while cursor <= end_date:
            items = grouped_map.get(cursor, [])
            date_rows.append(
                {
                    'date': cursor,
                    'label': cursor.strftime('%A'),
                    'short_label': cursor.strftime('%b %d'),
                    'count': len(items),
                    'appointments': items,
                }
            )
            cursor += timedelta(days=1)

        if date_rows:
            busiest_day = max(date_rows, key=lambda row: row['count'])
            busiest_day_label = busiest_day['short_label']
            busiest_day_count = busiest_day['count']
            average_daily_load = round(total_count / len(date_rows), 1)

    return {
        'appointment_rows': appointment_rows,
        'date_rows': date_rows,
        'total_count': total_count,
        'scheduled_count': scheduled_count,
        'completed_count': completed_count,
        'cancelled_count': cancelled_count,
        'upcoming_count': upcoming_count,
        'today_count': today_count,
        'unique_patient_count': len(unique_patient_ids),
        'active_staff_count': len(active_staff_ids),
        'current_local_time': timezone.localtime(now, tz),
        'next_appointment': next_appointment,
        'average_daily_load': average_daily_load,
        'busiest_day_label': busiest_day_label,
        'busiest_day_count': busiest_day_count,
    }


@login_required
def calendar_view(request):
    if not (request.user.is_superuser or request.user.groups.filter(name__in=ALLOWED_GROUPS).exists()):
        return HttpResponseForbidden('Role access required.')
    try:
        staff = request.user.staff
    except Staff.DoesNotExist:
        return HttpResponseForbidden('Staff access required.')

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')

    today = timezone.localdate(timezone.now(), tz)
    start_default_end = today + timedelta(days=7)
    start_date = _parse_date(request.GET.get('start'), today)
    end_date = _parse_date(request.GET.get('end'), start_date + timedelta(days=7))
    start_date, end_date = _normalize_date_range(start_date, end_date)
    staff_id = request.GET.get('staff')
    status = request.GET.get('status')

    with timezone.override(tz):
        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

        appointments = list(
            _filter_appointments(clinic, start_dt, end_dt, staff_id, status)
            .select_related('staff', 'patient', 'appointment_type')
            .order_by('start_at')
        )
        summary = _build_schedule_summary(appointments, tz, start_date, end_date)
        current_subscription = get_current_subscription(clinic)
        staff_list = list(Staff.objects.filter(clinic=clinic, is_active=True).select_related('user'))
        is_admin = request.user.is_superuser or request.user.groups.filter(name='Admin').exists()
        try:
            selected_staff_id = int(staff_id) if staff_id else None
        except (TypeError, ValueError):
            selected_staff_id = None
        selected_staff = next((member for member in staff_list if member.id == selected_staff_id), None)

        filter_badges = []
        if selected_staff:
            filter_badges.append(f'Staff: {selected_staff}')
        if status and status in dict(Appointment.Status.choices):
            filter_badges.append(f'Status: {status.title()}')
        if start_date != today or end_date != start_default_end:
            filter_badges.append(f'Range: {start_date:%b %d} to {end_date:%b %d}')

        context = {
            'clinic': clinic,
            'start_date': start_date,
            'end_date': end_date,
            'appointments': appointments,
            'appointment_rows': summary['appointment_rows'],
            'date_rows': summary['date_rows'],
            'total_count': summary['total_count'],
            'scheduled_count': summary['scheduled_count'],
            'completed_count': summary['completed_count'],
            'cancelled_count': summary['cancelled_count'],
            'upcoming_count': summary['upcoming_count'],
            'today_count': summary['today_count'],
            'unique_patient_count': summary['unique_patient_count'],
            'active_staff_count': summary['active_staff_count'],
            'current_local_time': summary['current_local_time'],
            'next_appointment': summary['next_appointment'],
            'average_daily_load': summary['average_daily_load'],
            'busiest_day_label': summary['busiest_day_label'],
            'busiest_day_count': summary['busiest_day_count'],
            'staff_list': staff_list,
            'selected_staff_id': staff_id or '',
            'selected_status': status or '',
            'filter_badges': filter_badges,
            'has_active_filters': bool(filter_badges),
            'current_subscription': current_subscription,
            'is_admin': is_admin,
        }
        return render(request, 'core/calendar.html', context)


@login_required
def dashboard_view(request):
    if not (request.user.is_superuser or request.user.groups.filter(name__in=ALLOWED_GROUPS).exists()):
        return HttpResponseForbidden('Role access required.')
    try:
        staff = request.user.staff
    except Staff.DoesNotExist:
        return HttpResponseForbidden('Staff access required.')

    if request.method == 'HEAD':
        return HttpResponse(status=200)

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')

    today = timezone.localdate(timezone.now(), tz)
    default_end_date = today + timedelta(days=6)
    start_date = _parse_date(request.GET.get('start'), today)
    end_date = _parse_date(request.GET.get('end'), default_end_date)
    start_date, end_date = _normalize_date_range(start_date, end_date)
    staff_id = request.GET.get('staff')
    status = request.GET.get('status')

    with timezone.override(tz):
        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

        appointments = list(
            _filter_appointments(clinic, start_dt, end_dt, staff_id, status)
            .select_related('staff', 'patient', 'appointment_type')
            .order_by('start_at')
        )

        now = timezone.now()
        total_count = len(appointments)
        completed_count = 0
        cancelled_count = 0
        upcoming_count = 0
        today_count = 0
        unique_patient_ids = set()
        active_staff_ids = set()
        today_appointments = []
        staff_load_map = {}

        chart_days = []
        cursor = start_date
        while cursor <= end_date:
            chart_days.append(cursor)
            cursor += timedelta(days=1)

        counts = {day: 0 for day in chart_days}
        next_appointment = None

        for appt in appointments:
            unique_patient_ids.add(appt.patient_id)
            active_staff_ids.add(appt.staff_id)

            if appt.status == Appointment.Status.COMPLETED:
                completed_count += 1
            elif appt.status == Appointment.Status.CANCELLED:
                cancelled_count += 1

            if appt.start_at >= now:
                upcoming_count += 1
                if next_appointment is None:
                    next_appointment = appt

            local_day = timezone.localtime(appt.start_at, tz).date()
            if local_day in counts:
                counts[local_day] += 1
            if local_day == today:
                today_count += 1
                today_appointments.append(appt)

            staff_row = staff_load_map.setdefault(
                appt.staff_id,
                {
                    'staff': appt.staff,
                    'count': 0,
                    'completed': 0,
                    'cancelled': 0,
                },
            )
            staff_row['count'] += 1
            if appt.status == Appointment.Status.COMPLETED:
                staff_row['completed'] += 1
            elif appt.status == Appointment.Status.CANCELLED:
                staff_row['cancelled'] += 1

        scheduled_count = total_count - completed_count - cancelled_count

        current_subscription = get_current_subscription(clinic)
        plan_usage = clinic_usage_summary(clinic)
        staff_list = list(
            Staff.objects.filter(clinic=clinic, is_active=True).select_related('user')
        )
        is_admin = request.user.is_superuser or request.user.groups.filter(name='Admin').exists()

        max_count = max(counts.values()) if counts else 0
        chart_points = []
        for day in chart_days:
            count = counts.get(day, 0)
            percent = int((count / max_count) * 100) if max_count else 0
            chart_points.append({
                'label': day.strftime('%b %d'),
                'count': count,
                'percent': percent,
            })

        unique_patients_count = len(unique_patient_ids)
        active_staff_count = len(active_staff_ids)
        completion_rate = int(round((completed_count / total_count) * 100)) if total_count else 0
        cancellation_rate = int(round((cancelled_count / total_count) * 100)) if total_count else 0
        average_daily_load = round(total_count / len(chart_days), 1) if chart_days else 0

        busiest_day = max(counts.items(), key=lambda item: item[1]) if counts else (start_date, 0)
        busiest_day_label = busiest_day[0].strftime('%b %d') if busiest_day[0] else ''
        busiest_day_count = busiest_day[1]

        staff_load_rows = sorted(
            staff_load_map.values(),
            key=lambda row: (-row['count'], str(row['staff']).lower()),
        )
        max_staff_load = staff_load_rows[0]['count'] if staff_load_rows else 0
        for row in staff_load_rows:
            row['scheduled'] = row['count'] - row['completed'] - row['cancelled']
            row['percent'] = int((row['count'] / max_staff_load) * 100) if max_staff_load else 0

        selected_staff = None
        try:
            selected_staff_id = int(staff_id) if staff_id else None
        except (TypeError, ValueError):
            selected_staff_id = None
        if selected_staff_id:
            selected_staff = next((member for member in staff_list if member.id == selected_staff_id), None)

        filter_badges = []
        if selected_staff:
            filter_badges.append(f'Staff: {selected_staff}')
        if status and status in dict(Appointment.Status.choices):
            filter_badges.append(f'Status: {status.title()}')
        if start_date != today or end_date != default_end_date:
            filter_badges.append(f'Range: {start_date:%b %d} to {end_date:%b %d}')

        appointment_preview = appointments[:8]
        remaining_appointment_count = max(total_count - len(appointment_preview), 0)

        context = {
            'clinic': clinic,
            'start_date': start_date,
            'end_date': end_date,
            'appointments': appointments,
            'appointment_preview': appointment_preview,
            'remaining_appointment_count': remaining_appointment_count,
            'today_appointments': today_appointments,
            'next_appointment': next_appointment,
            'total_count': total_count,
            'scheduled_count': scheduled_count,
            'completed_count': completed_count,
            'cancelled_count': cancelled_count,
            'upcoming_count': upcoming_count,
            'today_count': today_count,
            'unique_patients_count': unique_patients_count,
            'active_staff_count': active_staff_count,
            'completion_rate': completion_rate,
            'cancellation_rate': cancellation_rate,
            'average_daily_load': average_daily_load,
            'busiest_day_label': busiest_day_label,
            'busiest_day_count': busiest_day_count,
            'staff_load_rows': staff_load_rows,
            'filter_badges': filter_badges,
            'has_active_filters': bool(filter_badges),
            'current_local_time': timezone.localtime(now, tz),
            'staff_list': staff_list,
            'selected_staff_id': staff_id or '',
            'selected_status': status or '',
            'chart_points': chart_points,
            'current_subscription': current_subscription,
            'plan_usage': plan_usage,
            'is_admin': is_admin,
        }
        return render(request, 'core/dashboard.html', context)


@csrf_exempt
@xframe_options_exempt
def clinic_booking(request, clinic_id: int):
    clinic = get_object_or_404(Clinic, pk=clinic_id, is_active=True)
    return _clinic_booking(request, clinic)


@csrf_exempt
@xframe_options_exempt
def clinic_booking_slug(request, clinic_slug: str):
    clinic = get_object_or_404(Clinic, slug=clinic_slug, is_active=True)
    return _clinic_booking(request, clinic)


def _clinic_booking(request, clinic: Clinic):
    embed_mode = str(request.GET.get('embed') or request.POST.get('embed') or '').lower() in {'1', 'true', 'yes'}
    if settings.ENFORCE_SUBSCRIPTION and not clinic_has_active_subscription(clinic):
        return render(request, 'core/subscription_required.html', {'clinic': clinic, 'embed_mode': embed_mode})
    plan_usage = clinic_usage_summary(clinic)
    clinic_tz = ZoneInfo(clinic.timezone or 'UTC')
    staff_list = (
        Staff.objects.filter(clinic=clinic, is_active=True)
        .select_related('user')
        .order_by('user__last_name', 'user__first_name')
    )

    appointment_types = (
        AppointmentType.objects.filter(clinic=clinic, is_active=True)
        .order_by('name')
    )

    selected_type_id = (
        request.GET.get('type')
        or request.POST.get('appointment_type_id')
        or request.POST.get('type')
    )
    try:
        selected_type_id = int(selected_type_id) if selected_type_id else None
    except (TypeError, ValueError):
        selected_type_id = None

    selected_type = appointment_types.filter(id=selected_type_id).first() if selected_type_id else None
    if not selected_type and appointment_types.exists():
        selected_type = appointment_types.first()

    duration_minutes = (
        selected_type.duration_minutes
        if selected_type
        else getattr(settings, 'APPOINTMENT_SLOT_MINUTES', 30)
    )

    slots = build_available_slots(clinic, staff_list, duration_minutes=duration_minutes)
    slot_choices = [(slot.value, slot.label) for slot in slots]
    waitlist_form = WaitlistEntryForm(
        prefix='waitlist',
        initial={
            'preferred_start_date': timezone.localdate(timezone.now(), clinic_tz),
        },
    )
    waitlist_success = request.GET.get('waitlist') == 'joined'

    if request.method == 'POST':
        action = request.POST.get('form_action') or 'booking'
        if action == 'waitlist':
            waitlist_form = WaitlistEntryForm(request.POST, prefix='waitlist')
            form = BookingForm(
                slot_choices=slot_choices,
                appointment_type_id=selected_type.id if selected_type else None,
            )
            if waitlist_form.is_valid():
                email = waitlist_form.cleaned_data['email']
                waitlist_entry = waitlist_form.save(commit=False)
                waitlist_entry.clinic = clinic
                waitlist_entry.appointment_type = selected_type
                waitlist_entry.patient = Patient.objects.filter(clinic=clinic, email=email).first()
                waitlist_entry.save()
                _notify_clinic_waitlist_entry(waitlist_entry=waitlist_entry)

                query = {}
                if selected_type:
                    query['type'] = selected_type.id
                if embed_mode:
                    query['embed'] = '1'
                query['waitlist'] = 'joined'
                return redirect(f"{_clinic_booking_path(clinic)}?{urlencode(query)}")
        else:
            form = BookingForm(
                request.POST,
                slot_choices=slot_choices,
                appointment_type_id=selected_type.id if selected_type else None,
            )
            if form.is_valid():
                if not clinic_can_accept_appointment(clinic, usage=plan_usage):
                    form.add_error(
                        None,
                        _limit_reached_message(
                            item=plan_usage['appointments'],
                            resource_label='appointments',
                            action_label='booking new appointments',
                        ),
                    )
                else:
                    try:
                        staff_id, start_at = parse_slot_value(form.cleaned_data['slot'])
                    except ValueError:
                        form.add_error('slot', 'Selected slot is invalid. Please choose another.')
                    else:
                        staff = staff_list.filter(id=staff_id).first()
                        if not staff:
                            form.add_error('slot', 'Selected staff is not available.')
                        elif appointment_types.exists() and not selected_type:
                            form.add_error('appointment_type_id', 'Please choose an appointment type.')
                        else:
                            duration = timedelta(minutes=duration_minutes)
                            end_at = start_at + duration
                            start_at_local = timezone.localtime(start_at, clinic_tz)
                            email = form.cleaned_data['email'].strip().lower()
                            patient = None
                            patient_created = False

                            if request.user.is_authenticated:
                                patient = Patient.objects.filter(
                                    user=request.user,
                                    clinic=clinic,
                                ).first()
                                if not patient:
                                    patient = Patient.objects.filter(
                                        clinic=clinic,
                                        email=email,
                                    ).first()
                                    if patient and patient.user is None:
                                        patient.user = request.user
                                        patient.save(update_fields=['user'])
                            else:
                                patient = Patient.objects.filter(
                                    clinic=clinic,
                                    email=email,
                                ).first()

                            if not patient:
                                patient = Patient.objects.create(
                                    user=request.user if request.user.is_authenticated else None,
                                    clinic=clinic,
                                    first_name=form.cleaned_data['first_name'],
                                    last_name=form.cleaned_data['last_name'],
                                    email=email,
                                    phone=form.cleaned_data['phone'],
                                    dob=form.cleaned_data.get('dob'),
                                )
                                patient_created = True

                            appointment = Appointment(
                                clinic=clinic,
                                appointment_type=selected_type,
                                staff=staff,
                                patient=patient,
                                start_at=start_at,
                                end_at=end_at,
                                notes=form.cleaned_data.get('notes'),
                                intake_reason=form.cleaned_data.get('intake_reason'),
                            )
                            try:
                                appointment.save()
                            except ValidationError:
                                if patient_created:
                                    patient.delete()
                                form.add_error('slot', 'That time was just booked. Please choose another slot.')
                            else:
                                _send_appointment_email(
                                    request,
                                    appointment,
                                    subject_label='Appointment confirmed',
                                    headline='Your appointment is confirmed.',
                                    detail_copy='Use the self-service link below if you need to add intake details, cancel, or reschedule without calling the clinic.',
                                )
                                _notify_clinic_appointment_created(
                                    appointment=appointment,
                                    actor=request.user if request.user.is_authenticated else None,
                                    event_type=Notification.EventType.ONLINE_BOOKING_CREATED,
                                    title='Online booking received',
                                )
                                messages.success(request, 'Your appointment is booked.')
                                return render(
                                    request,
                                    'core/booking_success.html',
                                    {
                                        'clinic': clinic,
                                        'appointment': appointment,
                                        'appointment_local': start_at_local,
                                        'embed_mode': embed_mode,
                                        'booking_public_url': _clinic_booking_public_url(request, clinic),
                                        'manage_url': _appointment_manage_url(request, appointment),
                                    },
                                )
    else:
        form = BookingForm(
            slot_choices=slot_choices,
            appointment_type_id=selected_type.id if selected_type else None,
        )

    return render(
        request,
        'core/booking.html',
        {
            'clinic': clinic,
            'staff_list': staff_list,
            'appointment_types': appointment_types,
            'selected_type': selected_type,
            'form': form,
            'plan_usage': plan_usage,
            'slot_count': len(slot_choices),
            'embed_mode': embed_mode,
            'booking_public_url': _clinic_booking_public_url(request, clinic),
            'waitlist_form': waitlist_form,
            'waitlist_success': waitlist_success,
        },
    )


def _self_service_slot_choices(appointment: Appointment) -> list[tuple[str, str]]:
    staff_list = (
        Staff.objects.filter(clinic=appointment.clinic, is_active=True)
        .select_related('user')
        .order_by('user__last_name', 'user__first_name')
    )
    slots = build_available_slots(
        appointment.clinic,
        staff_list,
        duration_minutes=_appointment_duration_minutes(appointment),
        exclude_appointment_id=appointment.id,
    )
    return [(slot.value, f'{slot.label} • {slot.staff}') for slot in slots]


def _appointment_manage_status_message(status: str | None) -> tuple[str, str] | None:
    messages_map = {
        'intake-saved': ('flash--success', 'Your intake form and consent details were saved.'),
        'rescheduled': ('flash--success', 'Your appointment was rescheduled successfully.'),
        'cancelled': ('flash--success', 'Your appointment was cancelled.'),
        'message-sent': ('flash--success', 'Your message was sent to the clinic.'),
    }
    return messages_map.get(status or '')


def appointment_manage(request, token: str):
    appointment, token_error = _resolve_manage_appointment(token)
    if not appointment:
        return render(
            request,
            'core/appointment_manage.html',
            {
                'invalid_link': True,
                'error_message': token_error,
            },
            status=400,
        )

    clinic = appointment.clinic
    clinic_tz = ZoneInfo(clinic.timezone or 'UTC')
    appointment_local = timezone.localtime(appointment.start_at, clinic_tz)
    appointment_end_local = timezone.localtime(appointment.end_at, clinic_tz)
    can_modify = (
        appointment.status == Appointment.Status.SCHEDULED
        and appointment.start_at > timezone.now()
    )
    slot_choices = _self_service_slot_choices(appointment) if can_modify else []

    intake_initial = {
        'intake_reason': appointment.intake_reason or '',
        'intake_details': appointment.intake_details or '',
        'consent_to_treatment': appointment.consent_to_treatment,
        'consent_to_privacy': appointment.consent_to_privacy,
        'consent_signature_name': appointment.consent_signature_name or '',
    }
    intake_form = AppointmentSelfServiceIntakeForm(initial=intake_initial)
    reschedule_form = AppointmentSelfServiceRescheduleForm(slot_choices=slot_choices)
    cancel_form = AppointmentSelfServiceCancelForm(
        initial={'cancel_reason': appointment.cancel_reason or ''},
    )
    message_thread = MessageThread.objects.filter(appointment=appointment).select_related('patient', 'clinic').first()
    message_form = MessageReplyForm(prefix='message')

    if request.method == 'POST':
        action = request.POST.get('action') or ''
        if action == 'intake':
            intake_form = AppointmentSelfServiceIntakeForm(request.POST)
            if intake_form.is_valid():
                cleaned = intake_form.cleaned_data
                has_updates = any(
                    cleaned.get(field)
                    for field in (
                        'intake_reason',
                        'intake_details',
                        'consent_signature_name',
                        'consent_to_treatment',
                        'consent_to_privacy',
                    )
                )
                if not has_updates:
                    intake_form.add_error(None, 'Add intake details or consent selections before saving.')
                else:
                    appointment.intake_reason = cleaned.get('intake_reason')
                    appointment.intake_details = cleaned.get('intake_details')
                    appointment.consent_to_treatment = cleaned.get('consent_to_treatment')
                    appointment.consent_to_privacy = cleaned.get('consent_to_privacy')
                    appointment.consent_signature_name = cleaned.get('consent_signature_name')
                    appointment.consent_signed_at = timezone.now()
                    appointment.save(
                        update_fields=[
                            'intake_reason',
                            'intake_details',
                            'consent_to_treatment',
                            'consent_to_privacy',
                            'consent_signature_name',
                            'consent_signed_at',
                        ]
                    )
                    return redirect(f"{_appointment_manage_path(appointment)}?status=intake-saved")
        elif action == 'reschedule':
            if not can_modify:
                return render(
                    request,
                    'core/appointment_manage.html',
                    {
                        'appointment': appointment,
                        'appointment_local': appointment_local,
                        'appointment_end_local': appointment_end_local,
                        'can_modify': can_modify,
                        'status_banner': ('flash--danger', 'This appointment can no longer be rescheduled online.'),
                        'intake_form': intake_form,
                        'reschedule_form': reschedule_form,
                        'cancel_form': cancel_form,
                        'message_form': message_form,
                        'message_thread': message_thread,
                        'thread_messages': list(message_thread.messages.select_related('sender_user').order_by('created_at')) if message_thread else [],
                        'manage_url': _appointment_manage_url(request, appointment),
                    },
                )
            reschedule_form = AppointmentSelfServiceRescheduleForm(request.POST, slot_choices=slot_choices)
            if reschedule_form.is_valid():
                try:
                    staff_id, start_at = parse_slot_value(reschedule_form.cleaned_data['slot'])
                except ValueError:
                    reschedule_form.add_error('slot', 'Choose one of the current available slots.')
                else:
                    staff = (
                        Staff.objects.filter(
                            clinic=clinic,
                            is_active=True,
                            pk=staff_id,
                        )
                        .select_related('user')
                        .first()
                    )
                    if not staff:
                        reschedule_form.add_error('slot', 'That slot is no longer available.')
                    else:
                        duration_minutes = _appointment_duration_minutes(appointment)
                        appointment.staff = staff
                        appointment.start_at = start_at
                        appointment.end_at = start_at + timedelta(minutes=duration_minutes)
                        try:
                            appointment.save()
                        except ValidationError:
                            reschedule_form.add_error('slot', 'That slot was just booked. Choose another option.')
                        else:
                            _notify_clinic_appointment_updated(appointment=appointment)
                            _send_appointment_email(
                                request,
                                appointment,
                                subject_label='Appointment updated',
                                headline='Your appointment was rescheduled.',
                                detail_copy='Use the self-service link below if you need to make another change before the visit.',
                            )
                            return redirect(f"{_appointment_manage_path(appointment)}?status=rescheduled")
        elif action == 'cancel':
            if not can_modify:
                return render(
                    request,
                    'core/appointment_manage.html',
                    {
                        'appointment': appointment,
                        'appointment_local': appointment_local,
                        'appointment_end_local': appointment_end_local,
                        'can_modify': can_modify,
                        'status_banner': ('flash--danger', 'This appointment can no longer be cancelled online.'),
                        'intake_form': intake_form,
                        'reschedule_form': reschedule_form,
                        'cancel_form': cancel_form,
                        'message_form': message_form,
                        'message_thread': message_thread,
                        'thread_messages': list(message_thread.messages.select_related('sender_user').order_by('created_at')) if message_thread else [],
                        'manage_url': _appointment_manage_url(request, appointment),
                    },
                )
            cancel_form = AppointmentSelfServiceCancelForm(request.POST)
            if cancel_form.is_valid():
                appointment.status = Appointment.Status.CANCELLED
                appointment.cancel_reason = cancel_form.cleaned_data.get('cancel_reason')
                appointment.save(update_fields=['status', 'cancel_reason'])
                _notify_clinic_appointment_cancelled(appointment=appointment)
                _send_appointment_email(
                    request,
                    appointment,
                    subject_label='Appointment cancelled',
                    headline='Your appointment was cancelled.',
                    detail_copy='If you still need an appointment, you can return to the clinic booking page to choose a new slot.',
                )
                return redirect(f"{_appointment_manage_path(appointment)}?status=cancelled")
        elif action == 'message':
            message_form = MessageReplyForm(request.POST, prefix='message')
            if message_form.is_valid():
                message_thread = get_or_create_appointment_thread(appointment)
                patient_sender = appointment.patient.user if appointment.patient.user_id else None
                message = append_message(
                    message_thread,
                    sender_type=Message.SenderType.PATIENT,
                    sender_user=patient_sender,
                    sender_label=patient_display_name(appointment.patient),
                    body=message_form.cleaned_data['body'],
                )
                _send_clinic_message_alert(request=request, thread=message_thread, message=message)
                return redirect(f"{_appointment_manage_path(appointment)}?status=message-sent")

    thread_messages = []
    if message_thread is not None:
        thread_messages = list(message_thread.messages.select_related('sender_user').order_by('created_at'))

    return render(
        request,
        'core/appointment_manage.html',
        {
            'appointment': appointment,
            'appointment_local': appointment_local,
            'appointment_end_local': appointment_end_local,
            'can_modify': can_modify,
            'status_banner': _appointment_manage_status_message(request.GET.get('status')),
            'intake_form': intake_form,
            'reschedule_form': reschedule_form,
            'cancel_form': cancel_form,
            'message_form': message_form,
            'message_thread': message_thread,
            'thread_messages': thread_messages,
            'manage_url': _appointment_manage_url(request, appointment),
        },
    )


def appointment_lookup(request):
    appointment = None
    appointment_local = None
    error_message = None
    manage_url = ''

    if request.method == 'POST':
        form = AppointmentLookupForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email'].strip().lower()
            code = form.cleaned_data['confirmation_code'].strip().upper()
            appointment = (
                Appointment.objects.filter(confirmation_code=code)
                .select_related('clinic', 'staff', 'patient')
                .first()
            )
            if not appointment:
                error_message = 'No appointment found for that confirmation code.'
            elif appointment.patient.email.lower() != email:
                appointment = None
                error_message = 'Email does not match our records for that appointment.'
            elif appointment.start_at < timezone.now():
                error_message = 'That appointment is in the past.'
            else:
                appointment_local = timezone.localtime(
                    appointment.start_at,
                    ZoneInfo(appointment.clinic.timezone or 'UTC'),
                )
                manage_url = _appointment_manage_url(request, appointment)
    else:
        form = AppointmentLookupForm()

    return render(
        request,
        'core/appointment_lookup.html',
        {
            'form': form,
            'appointment': appointment,
            'appointment_local': appointment_local,
            'error_message': error_message,
            'manage_url': manage_url,
        },
    )


def _message_status_banner(status: str | None) -> tuple[str, str] | None:
    status_map = {
        'sent': ('flash--success', 'Your message was sent.'),
        'thread-created': ('flash--success', 'Your conversation was started successfully.'),
    }
    return status_map.get(status or '')


def _message_list_rows(threads, *, user, tz, title_fn, meta_fn):
    thread_ids = [thread.id for thread in threads]
    read_map = read_map_for_user(user, thread_ids)
    rows = []
    for thread in threads:
        rows.append(
            {
                'thread': thread,
                'title': title_fn(thread),
                'meta': meta_fn(thread),
                'updated_local': timezone.localtime(thread.last_message_at, tz),
                'is_unread': thread_is_unread_for_user(thread, user, read_map),
            }
        )
    return rows


@login_required
def messages_view(request, thread_id: int | None = None):
    staff = None
    patient = None
    clinic = None
    profile_type = None

    try:
        staff = request.user.staff
        clinic = staff.clinic
        profile_type = 'staff'
    except Staff.DoesNotExist:
        patient, profiles = _get_active_patient_profile(request)
        if not patient:
            return HttpResponseForbidden('Messaging access requires a staff or patient profile.')
        clinic = patient.clinic
        profile_type = 'patient'

    tz = ZoneInfo(clinic.timezone or 'UTC')
    status_banner = _message_status_banner(request.GET.get('status'))
    selected_thread = None
    reply_form = MessageReplyForm(prefix='reply')
    compose_form = MessageComposeForm(prefix='compose')
    can_reply = False
    can_manage_permissions = False

    if profile_type == 'staff':
        if not user_can_view_messages(request.user, clinic):
            return HttpResponseForbidden('This staff role does not have messaging access.')
        threads = list(message_threads_for_staff(clinic))
        thread_rows = _message_list_rows(
            threads,
            user=request.user,
            tz=tz,
            title_fn=thread_title_for_staff,
            meta_fn=thread_meta_for_staff,
        )
        can_reply = user_can_reply_messages(request.user, clinic)
        can_manage_permissions = user_can_manage_messaging_settings(request.user, clinic)
    else:
        threads = list(message_threads_for_patient(patient))
        thread_rows = _message_list_rows(
            threads,
            user=request.user,
            tz=tz,
            title_fn=thread_title_for_patient,
            meta_fn=thread_meta_for_patient,
        )
        can_reply = True
        can_manage_permissions = False

    if thread_id:
        selected_thread = get_object_or_404(
            MessageThread.objects.select_related('patient', 'clinic', 'appointment__appointment_type'),
            pk=thread_id,
        )
        if profile_type == 'staff':
            if selected_thread.clinic_id != clinic.id:
                return HttpResponseForbidden('Thread access denied for this clinic.')
        else:
            if selected_thread.patient_id != patient.id:
                return HttpResponseForbidden('Thread access denied for this patient.')
    elif threads:
        selected_thread = threads[0]

    if request.method == 'POST':
        action = request.POST.get('action') or ''
        if profile_type == 'patient' and action == 'new-thread':
            compose_form = MessageComposeForm(request.POST, prefix='compose')
            if compose_form.is_valid():
                new_thread = create_patient_portal_thread(
                    patient=patient,
                    subject=compose_form.cleaned_data['subject'],
                    body=compose_form.cleaned_data['body'],
                    sender_user=request.user,
                )
                mark_thread_read(new_thread, request.user)
                first_message = new_thread.messages.first()
                if first_message:
                    _send_clinic_message_alert(request=request, thread=new_thread, message=first_message)
                return redirect(f"{reverse('messages-thread', args=[new_thread.id])}?status=thread-created")
        elif action == 'reply' and selected_thread is not None:
            reply_form = MessageReplyForm(request.POST, prefix='reply')
            if reply_form.is_valid():
                if profile_type == 'staff':
                    if not can_reply:
                        return HttpResponseForbidden('This staff role is view-only for messaging.')
                    message = append_message(
                        selected_thread,
                        sender_type=Message.SenderType.STAFF,
                        sender_user=request.user,
                        sender_label=staff_display_name(request.user),
                        body=reply_form.cleaned_data['body'],
                    )
                    mark_thread_read(selected_thread, request.user)
                    _send_patient_message_notice(request=request, thread=selected_thread, message=message)
                else:
                    if selected_thread.status != MessageThread.Status.OPEN:
                        return HttpResponseForbidden('This conversation is closed.')
                    message = append_message(
                        selected_thread,
                        sender_type=Message.SenderType.PATIENT,
                        sender_user=request.user,
                        sender_label=patient_display_name(patient),
                        body=reply_form.cleaned_data['body'],
                    )
                    mark_thread_read(selected_thread, request.user)
                    _send_clinic_message_alert(request=request, thread=selected_thread, message=message)
                return redirect(f"{reverse('messages-thread', args=[selected_thread.id])}?status=sent")

    selected_messages = []
    selected_thread_title = ''
    selected_thread_meta = ''
    if selected_thread is not None:
        mark_thread_read(selected_thread, request.user)
        selected_messages = list(selected_thread.messages.select_related('sender_user').order_by('created_at'))
        if profile_type == 'staff':
            selected_thread_title = thread_title_for_staff(selected_thread)
            selected_thread_meta = thread_meta_for_staff(selected_thread)
        else:
            selected_thread_title = thread_title_for_patient(selected_thread)
            selected_thread_meta = thread_meta_for_patient(selected_thread)

    return render(
        request,
        'core/messages.html',
        {
            'clinic': clinic,
            'patient': patient,
            'staff_profile': staff,
            'profile_type': profile_type,
            'thread_rows': thread_rows,
            'selected_thread': selected_thread,
            'selected_thread_title': selected_thread_title,
            'selected_thread_meta': selected_thread_meta,
            'selected_messages': selected_messages,
            'reply_form': reply_form,
            'compose_form': compose_form,
            'status_banner': status_banner,
            'can_reply': can_reply,
            'can_manage_permissions': can_manage_permissions,
            'current_local_time': timezone.localtime(timezone.now(), tz),
        },
    )


def verify_email(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (User.DoesNotExist, ValueError, TypeError):
        user = None

    if user and default_token_generator.check_token(user, token):
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])
        return render(request, 'core/verify_email.html', {'status': 'ok'})

    return render(request, 'core/verify_email.html', {'status': 'invalid'})


def resend_verification(request):
    initial_email = request.GET.get('email', '')
    if request.method == 'POST':
        form = ResendVerificationForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            user = (
                User.objects.filter(username__iexact=email).first()
                or User.objects.filter(email__iexact=email).first()
            )
            if user and not user.is_active:
                _send_verification_email(request, user, clinic=_get_user_clinic(user))
            return render(
                request,
                'core/resend_verification_success.html',
                {
                    'email': email,
                },
            )
    else:
        form = ResendVerificationForm(initial={'email': initial_email})

    return render(
        request,
        'core/resend_verification.html',
        {
            'form': form,
            'sent': False,
        },
    )


@login_required
def post_login_redirect(request):
    if request.user.is_superuser or request.user.groups.filter(name__in=ALLOWED_GROUPS).exists():
        return redirect('dashboard')
    if Patient.objects.filter(user=request.user).exists():
        return redirect('patient-portal')
    return redirect('login')


@login_required
def settings_view(request):
    clinic = None
    profile = None
    profile_type = None
    try:
        profile = request.user.staff
        clinic = profile.clinic
        profile_type = 'staff'
    except Staff.DoesNotExist:
        patient, profiles = _get_active_patient_profile(request)
        if patient:
            profile = patient
            clinic = patient.clinic
            profile_type = 'patient'

    avatar_url = None
    if profile and getattr(profile, 'avatar', None):
        try:
            if profile.avatar:
                avatar_url = profile.avatar.url
        except Exception:
            avatar_url = None

    avatar_saved = False
    messaging_permissions_saved = False
    messaging_form = None

    can_manage_messaging_permissions = bool(
        clinic and profile_type == 'staff' and user_can_manage_messaging_settings(request.user, clinic)
    )
    if can_manage_messaging_permissions:
        messaging_form = MessagingRolePermissionForm(initial_access=clinic_messaging_access_map(clinic))

    if request.method == 'POST':
        settings_action = request.POST.get('settings_action') or 'avatar'
        if settings_action == 'messaging-permissions' and can_manage_messaging_permissions:
            messaging_form = MessagingRolePermissionForm(
                request.POST,
                initial_access=clinic_messaging_access_map(clinic),
            )
            form = AvatarUploadForm()
            if messaging_form.is_valid():
                for role, access_level in messaging_form.cleaned_role_access_map().items():
                    ClinicMessagingPermission.objects.update_or_create(
                        clinic=clinic,
                        role=role,
                        defaults={'access_level': access_level},
                    )
                messaging_permissions_saved = True
                messaging_form = MessagingRolePermissionForm(initial_access=clinic_messaging_access_map(clinic))
        else:
            form = AvatarUploadForm(request.POST, request.FILES)
            if form.is_valid() and profile is not None:
                avatar = form.cleaned_data.get('avatar')
                if avatar:
                    profile.avatar = avatar
                    profile.save(update_fields=['avatar'])
                    avatar_saved = True
    else:
        form = AvatarUploadForm()

    groups = list(request.user.groups.values_list('name', flat=True))
    booking_public_url = _clinic_booking_public_url(request, clinic) if clinic and profile_type == 'staff' else ''
    booking_embed_url = ''
    booking_embed_code = ''
    can_manage_booking_embed = bool(clinic and profile_type == 'staff' and _is_admin(request.user))
    can_view_clinic_security_activity = bool(clinic and profile_type == 'staff' and _is_admin(request.user))
    can_manage_two_factor = user_can_manage_two_factor(request.user)
    two_factor_enabled = user_has_confirmed_two_factor(request.user)
    two_factor_recovery_codes = recovery_code_count(request.user) if two_factor_enabled else 0
    if can_manage_booking_embed:
        booking_embed_url = _clinic_booking_embed_url(request, clinic)
        booking_embed_code = _clinic_booking_embed_code(request, clinic)
    tz = ZoneInfo(clinic.timezone or 'UTC') if clinic else timezone.get_current_timezone()
    recent_user_security_events = list(
        SecurityEvent.objects.filter(user=request.user)
        .order_by('-created_at')[:6]
    )
    for event in recent_user_security_events:
        event.created_local = timezone.localtime(event.created_at, tz)
    recent_clinic_security_events = []
    if can_view_clinic_security_activity:
        recent_clinic_security_events = list(
            SecurityEvent.objects.filter(clinic=clinic)
            .select_related('user')
            .order_by('-created_at')[:10]
        )
        for event in recent_clinic_security_events:
            event.created_local = timezone.localtime(event.created_at, tz)
    return render(
        request,
        'core/settings.html',
        {
            'clinic': clinic,
            'groups': groups,
            'avatar_form': form,
            'avatar_saved': avatar_saved,
            'avatar_url': avatar_url,
            'profile_type': profile_type,
            'can_manage_booking_embed': can_manage_booking_embed,
            'can_view_clinic_security_activity': can_view_clinic_security_activity,
            'can_manage_messaging_permissions': can_manage_messaging_permissions,
            'messaging_permissions_form': messaging_form,
            'messaging_permissions_saved': messaging_permissions_saved,
            'messaging_role_rows': messaging_role_rows(clinic) if clinic and profile_type == 'staff' else [],
            'is_clinic_owner': bool(clinic and user_is_clinic_owner(request.user, clinic)),
            'booking_public_url': booking_public_url,
            'booking_embed_url': booking_embed_url,
            'booking_embed_code': booking_embed_code,
            'can_manage_two_factor': can_manage_two_factor,
            'two_factor_enabled': two_factor_enabled,
            'two_factor_required': request.user.is_superuser,
            'two_factor_verified': request.user.is_verified() if hasattr(request.user, 'is_verified') else False,
            'two_factor_recovery_codes': two_factor_recovery_codes,
            'security_audit_url': reverse('security-audit') if can_view_clinic_security_activity else '',
            'recent_user_security_events': recent_user_security_events,
            'recent_clinic_security_events': recent_clinic_security_events,
            'current_local_time': timezone.localtime(timezone.now(), tz),
        },
    )


@login_required
def security_audit_view(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error
    if not _is_admin(request.user):
        return HttpResponseForbidden('Only clinic admins can view the security audit trail.')

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    query = (request.GET.get('q') or '').strip()
    role = (request.GET.get('role') or '').strip()
    event_type = (request.GET.get('event_type') or '').strip()
    sort = (request.GET.get('sort') or 'newest').strip()
    date_from = (request.GET.get('date_from') or '').strip()
    date_to = (request.GET.get('date_to') or '').strip()
    country = (request.GET.get('country') or '').strip().upper()

    events = SecurityEvent.objects.filter(clinic=clinic).select_related('user')

    if query:
        events = events.filter(
            Q(identifier__icontains=query)
            | Q(user__username__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
        )

    valid_roles = ['Admin', 'Doctor', 'Nurse', 'FrontDesk']
    if role in valid_roles:
        events = events.filter(user__groups__name=role)

    valid_event_types = {choice[0] for choice in SecurityEvent.EventType.choices}
    if event_type in valid_event_types:
        events = events.filter(event_type=event_type)

    if country:
        if len(country) == 2 and country.isalpha():
            events = events.filter(country_code=country)
        else:
            country = ''

    if date_from:
        try:
            events = events.filter(created_at__date__gte=date.fromisoformat(date_from))
        except ValueError:
            date_from = ''
    if date_to:
        try:
            events = events.filter(created_at__date__lte=date.fromisoformat(date_to))
        except ValueError:
            date_to = ''

    if sort == 'oldest':
        events = events.order_by('created_at').distinct()
    else:
        sort = 'newest'
        events = events.order_by('-created_at').distinct()

    audit_rows = list(events[:150])
    for event in audit_rows:
        event.created_local = timezone.localtime(event.created_at, tz)
        event.role_label = _staff_role_for_user(event.user) if event.user else ''

    recent_scope = SecurityEvent.objects.filter(clinic=clinic)
    summary = {
        'total_events': recent_scope.count(),
        'login_success_count': recent_scope.filter(event_type=SecurityEvent.EventType.LOGIN_SUCCESS).count(),
        'login_failed_count': recent_scope.filter(event_type=SecurityEvent.EventType.LOGIN_FAILED).count(),
        'password_changed_count': recent_scope.filter(event_type=SecurityEvent.EventType.PASSWORD_CHANGED).count(),
        'rate_limited_count': recent_scope.filter(event_type=SecurityEvent.EventType.RATE_LIMITED).count(),
        'access_blocked_count': recent_scope.filter(event_type=SecurityEvent.EventType.ACCESS_BLOCKED).count(),
    }

    return render(
        request,
        'core/security_audit.html',
        {
            'clinic': clinic,
            'current_local_time': timezone.localtime(timezone.now(), tz),
            'audit_rows': audit_rows,
            'summary': summary,
            'filters': {
                'q': query,
                'role': role,
                'event_type': event_type,
                'sort': sort,
                'date_from': date_from,
                'date_to': date_to,
                'country': country,
            },
            'role_choices': valid_roles,
            'event_type_choices': SecurityEvent.EventType.choices,
        },
    )


@login_required
def portal_search(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    if not (_is_admin(request.user) or _is_frontdesk(request.user) or _is_doctor(request.user)):
        return HttpResponseForbidden('Search is available to Admin, Front Desk, and Doctor roles only.')

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    query = (request.GET.get('q') or '').strip()
    appointments = []
    patients = []

    if query:
        exact_appointment, appointments, patients = _perform_portal_search(
            staff=staff,
            query=query,
            appointment_limit=8,
            patient_limit=8,
        )
        if exact_appointment:
            return redirect('staff-appointment-edit', appointment_id=exact_appointment.id)

    appointment_results = []
    for appointment in appointments:
        appointment_results.append(
            {
                'appointment': appointment,
                'patient_label': _patient_label(appointment.patient),
                'local_start': timezone.localtime(appointment.start_at, tz),
                'local_end': timezone.localtime(appointment.end_at, tz),
                'service_name': appointment.appointment_type.name if appointment.appointment_type else 'General appointment',
            }
        )

    patient_results = []
    for patient in patients:
        next_appointment = (
            _searchable_appointments_for_staff(staff)
            .filter(patient=patient, start_at__gte=timezone.now())
            .order_by('start_at')
            .first()
        )
        patient_results.append(
            {
                'patient': patient,
                'next_appointment_local': timezone.localtime(next_appointment.start_at, tz) if next_appointment else None,
            }
        )

    return render(
        request,
        'core/portal_search.html',
        {
            'clinic': clinic,
            'query': query,
            'appointment_results': appointment_results,
            'patient_results': patient_results,
            'has_results': bool(appointment_results or patient_results),
            'current_local_time': timezone.localtime(timezone.now(), tz),
        },
    )


@login_required
def portal_search_preview(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    if not (_is_admin(request.user) or _is_frontdesk(request.user) or _is_doctor(request.user)):
        return HttpResponseForbidden('Search is available to Admin, Front Desk, and Doctor roles only.')

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    query = (request.GET.get('q') or '').strip()
    search_url = f"{reverse('portal-search')}?{urlencode({'q': query})}" if query else reverse('portal-search')

    if len(query) < 2:
        return JsonResponse(
            {
                'ok': True,
                'query': query,
                'appointments': [],
                'patients': [],
                'search_url': search_url,
            }
        )

    _, appointments, patients = _perform_portal_search(
        staff=staff,
        query=query,
        appointment_limit=5,
        patient_limit=5,
        candidate_limit=200,
    )

    appointment_results = []
    for appointment in appointments:
        local_start = timezone.localtime(appointment.start_at, tz)
        appointment_results.append(
            {
                'title': _patient_label(appointment.patient),
                'subtitle': f'Confirmation {appointment.confirmation_code}',
                'meta': (
                    f'{local_start:%b %d, %Y %I:%M %p} · '
                    f'{appointment.appointment_type.name if appointment.appointment_type else "General appointment"}'
                ),
                'href': reverse('staff-appointment-edit', args=[appointment.id]),
            }
        )

    patient_results = []
    for patient in patients:
        next_appointment = (
            _searchable_appointments_for_staff(staff)
            .filter(patient=patient, start_at__gte=timezone.now())
            .order_by('start_at')
            .first()
        )
        if next_appointment:
            next_label = timezone.localtime(next_appointment.start_at, tz).strftime('%b %d, %Y %I:%M %p')
            meta = f'Next appointment {next_label}'
        else:
            meta = 'Patient record'
        patient_results.append(
            {
                'title': _patient_label(patient),
                'subtitle': patient.email or patient.phone or f'Patient {patient.id}',
                'meta': meta,
                'href': reverse('staff-patient-edit', args=[patient.id]),
            }
        )

    return JsonResponse(
        {
            'ok': True,
            'query': query,
            'appointments': appointment_results,
            'patients': patient_results,
            'search_url': search_url,
        }
    )


def clinic_signup(request):
    plans = Plan.objects.filter(is_active=True).order_by('price_cents')
    clinic = None
    current_subscription = None
    plan_usage = None
    clinic_id = request.session.get('signup_clinic_id')
    if clinic_id:
        clinic = Clinic.objects.filter(id=clinic_id).first()
        if clinic:
            current_subscription = get_current_subscription(clinic)
            plan_usage = clinic_usage_summary(clinic)

    if request.method == 'POST' and not clinic:
        form = ClinicSignupForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['admin_email']
            if Clinic.objects.filter(name=form.cleaned_data['clinic_name']).exists():
                form.add_error('clinic_name', 'Clinic name already exists.')
            elif User.objects.filter(username=email).exists():
                form.add_error('admin_email', 'An account with this email already exists.')
            else:
                clinic = Clinic.objects.create(
                    name=form.cleaned_data['clinic_name'],
                    timezone=form.cleaned_data.get('timezone') or 'UTC',
                    email=email,
                    brand_color='#1d4ed8',
                )
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    first_name=form.cleaned_data['admin_first_name'],
                    last_name=form.cleaned_data['admin_last_name'],
                    password=form.cleaned_data['password'],
                    is_staff=True,
                    is_active=False,
                )
                Staff.objects.create(user=user, clinic=clinic)
                admin_group = Group.objects.filter(name='Admin').first()
                if admin_group:
                    user.groups.add(admin_group)
                clinic.owner_user = user
                clinic.save(update_fields=['owner_user'])

                request.session['signup_clinic_id'] = clinic.id
                _send_verification_email(request, user, clinic=clinic)
                _send_platform_clinic_signup_alert(request=request, clinic=clinic, owner_user=user)
                return render(
                    request,
                    'core/clinic_signup.html',
                    {
                        'form': ClinicSignupForm(),
                        'clinic': clinic,
                        'plans': plans,
                        'current_subscription': current_subscription,
                        'plan_usage': plan_usage,
                        'paypal_client_id': settings.PAYPAL_CLIENT_ID,
                        'paypal_sdk_url': settings.PAYPAL_SDK_URL,
                        'verification_sent': True,
                    },
                )
    else:
        form = ClinicSignupForm()

    return render(
        request,
        'core/clinic_signup.html',
        {
            'form': form,
            'clinic': clinic,
            'plans': plans,
            'current_subscription': current_subscription,
            'plan_usage': plan_usage,
            'paypal_client_id': settings.PAYPAL_CLIENT_ID,
            'paypal_sdk_url': settings.PAYPAL_SDK_URL,
            'verification_sent': bool(clinic),
        },
    )


@never_cache
def patient_signup(request, clinic_id: int):
    clinic = get_object_or_404(Clinic, pk=clinic_id, is_active=True)
    return _patient_signup(request, clinic)


@never_cache
def patient_signup_slug(request, clinic_slug: str):
    clinic = get_object_or_404(Clinic, slug=clinic_slug, is_active=True)
    return _patient_signup(request, clinic)


def _patient_signup(request, clinic: Clinic):
    if request.method == 'POST':
        form = PatientSignupForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            user = User.objects.filter(username=email).first()
            verification_sent = False

            if user:
                if Patient.objects.filter(user=user, clinic=clinic).exists():
                    form.add_error('email', 'You already have an account for this clinic. Please sign in.')
                else:
                    patient = Patient.objects.create(
                        user=user,
                        clinic=clinic,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        email=email,
                        phone=form.cleaned_data['phone'],
                        dob=form.cleaned_data.get('dob'),
                    )
                    if not user.is_active:
                        _send_verification_email(request, user, clinic=clinic)
                        verification_sent = True
                    _notify_clinic_patient_signup(clinic=clinic, patient=patient)
                    return render(
                        request,
                        'core/patient_signup_success.html',
                        {
                            'clinic': clinic,
                            'email': email,
                            'verification_sent': verification_sent,
                        },
                    )
            else:
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    password=form.cleaned_data['password'],
                    is_active=False,
                )
                patient_group = Group.objects.filter(name='Patient').first()
                if patient_group:
                    user.groups.add(patient_group)
                patient = Patient.objects.create(
                    user=user,
                    clinic=clinic,
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    email=email,
                    phone=form.cleaned_data['phone'],
                    dob=form.cleaned_data.get('dob'),
                )
                _send_verification_email(request, user, clinic=clinic)
                _notify_clinic_patient_signup(clinic=clinic, patient=patient)
                return render(
                    request,
                    'core/patient_signup_success.html',
                    {
                        'clinic': clinic,
                        'email': email,
                        'verification_sent': True,
                    },
                )
    else:
        form = PatientSignupForm()

    return render(
        request,
        'core/patient_signup.html',
        {
            'clinic': clinic,
            'form': form,
            'verification_sent': False,
        },
    )


@login_required
def patient_portal(request):
    profiles = Patient.objects.filter(user=request.user).select_related('clinic')
    if not profiles.exists():
        return HttpResponseForbidden('Patient access required.')

    if request.method == 'POST':
        clinic_id = request.POST.get('clinic_id')
        if clinic_id:
            try:
                clinic_id = int(clinic_id)
            except (TypeError, ValueError):
                clinic_id = None
            if clinic_id and profiles.filter(clinic_id=clinic_id).exists():
                request.session['patient_clinic_id'] = clinic_id
        return redirect('patient-portal')

    if request.GET.get('switch'):
        request.session.pop('patient_clinic_id', None)

    clinic_id = request.GET.get('clinic')
    if clinic_id:
        try:
            clinic_id = int(clinic_id)
        except (TypeError, ValueError):
            clinic_id = None
        if clinic_id and profiles.filter(clinic_id=clinic_id).exists():
            request.session['patient_clinic_id'] = clinic_id

    patient, profiles = _get_active_patient_profile(request)
    if patient is None:
        return render(
            request,
            'core/patient_select_clinic.html',
            {
                'profiles': profiles,
                'profile_count': profiles.count(),
            },
        )

    clinic = patient.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    upcoming = (
        Appointment.objects.filter(patient=patient, start_at__gte=timezone.now())
        .select_related('staff', 'clinic', 'appointment_type')
        .order_by('start_at')
    )
    appointments = []
    for appt in upcoming:
        appointments.append(
            {
                'start_at': timezone.localtime(appt.start_at, tz),
                'end_at': timezone.localtime(appt.end_at, tz),
                'staff': appt.staff,
                'status': appt.status,
                'appointment_type': appt.appointment_type,
            }
        )

    return render(
        request,
        'core/patient_portal.html',
        {
            'clinic': clinic,
            'patient': patient,
            'appointments': appointments,
            'appointment_count': len(appointments),
            'next_appointment': appointments[0] if appointments else None,
            'current_local_time': timezone.localtime(timezone.now(), tz),
        },
    )


@login_required
def notifications_view(request):
    notifications = list(
        Notification.objects.filter(recipient=request.user)
        .select_related('clinic', 'actor')
        .order_by('-created_at')
    )
    default_tz = timezone.get_current_timezone()
    notification_rows = [
        {
            'item': notification,
            'created_at_local': timezone.localtime(
                notification.created_at,
                ZoneInfo(notification.clinic.timezone or 'UTC') if notification.clinic else default_tz,
            ),
        }
        for notification in notifications
    ]
    unread_rows = [row for row in notification_rows if not row['item'].is_read]
    read_rows = [row for row in notification_rows if row['item'].is_read]

    return render(
        request,
        'core/notifications.html',
        {
            'notification_rows': notification_rows,
            'unread_rows': unread_rows,
            'read_rows': read_rows,
            'total_notifications': len(notification_rows),
            'unread_total': len(unread_rows),
            'read_total': len(read_rows),
        },
    )


@login_required
def notification_open(request, notification_id: int):
    notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
    notification.mark_read()

    resolved_url, info_message = _resolve_notification_destination(notification, request.user)
    next_url = resolved_url or request.GET.get('next') or notification.link or reverse('notifications')
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse('notifications')
    if info_message:
        messages.info(request, info_message)
    return redirect(next_url)


@login_required
@require_POST
def notification_mark_read(request, notification_id: int):
    notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
    notification.mark_read()
    return redirect(request.POST.get('next') or 'notifications')


@login_required
@require_POST
def notifications_mark_all_read(request):
    unread_qs = Notification.objects.filter(recipient=request.user, is_read=False)
    unread_count = unread_qs.count()
    if unread_count:
        unread_qs.update(is_read=True, read_at=timezone.now())
        messages.success(request, f'{unread_count} notification{"s" if unread_count != 1 else ""} marked as read.')
    return redirect(request.POST.get('next') or 'notifications')


@login_required
def staff_appointments(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    plan_usage = clinic_usage_summary(clinic)
    can_create = _is_admin(request.user) or _is_frontdesk(request.user)
    open_create_modal = False
    walkin_form = _build_walk_in_form(clinic=clinic, prefix='walkin')

    if request.method == 'POST':
        if not can_create:
            return HttpResponseForbidden('Only Admin or Front Desk can create walk-in appointments.')
        walkin_form = _build_walk_in_form(clinic=clinic, data=request.POST, prefix='walkin')
        if walkin_form.is_valid():
            if not clinic_can_accept_appointment(clinic, usage=plan_usage):
                walkin_form.add_error(
                    None,
                    _limit_reached_message(
                        item=plan_usage['appointments'],
                        resource_label='appointments',
                        action_label='creating more appointments',
                    ),
                )
            else:
                appointment = _save_walk_in_appointment(walkin_form, clinic, tz)
                if appointment:
                    _notify_clinic_appointment_created(appointment=appointment, actor=request.user)
                    messages.success(request, f'Appointment added: {_patient_label(appointment.patient)}.')
                    return redirect(request.get_full_path())
        open_create_modal = True

    today = timezone.localdate(timezone.now(), tz)
    start_default_end = today + timedelta(days=7)
    start_date = _parse_date(request.GET.get('start'), today)
    end_date = _parse_date(request.GET.get('end'), start_date + timedelta(days=7))
    start_date, end_date = _normalize_date_range(start_date, end_date)
    status = request.GET.get('status') or ''

    with timezone.override(tz):
        start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
        end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))
        qs = Appointment.objects.filter(
            clinic=clinic,
            start_at__gte=start_dt,
            start_at__lte=end_dt,
        ).select_related('staff', 'patient', 'appointment_type')

        if _is_doctor(request.user) and not _is_admin(request.user):
            qs = qs.filter(staff=staff)

        if status and status in dict(Appointment.Status.choices):
            qs = qs.filter(status=status)

        appointments = list(qs.order_by('start_at'))

    summary = _build_schedule_summary(appointments, tz, start_date, end_date)
    filter_badges = []
    if status and status in dict(Appointment.Status.choices):
        filter_badges.append(f'Status: {status.title()}')
    if start_date != today or end_date != start_default_end:
        filter_badges.append(f'Range: {start_date:%b %d} to {end_date:%b %d}')

    return render(
        request,
        'core/staff_appointments.html',
        {
            'clinic': clinic,
            'appointments': appointments,
            'appointment_rows': summary['appointment_rows'],
            'start_date': start_date,
            'end_date': end_date,
            'selected_status': status,
            'total_count': summary['total_count'],
            'scheduled_count': summary['scheduled_count'],
            'completed_count': summary['completed_count'],
            'cancelled_count': summary['cancelled_count'],
            'upcoming_count': summary['upcoming_count'],
            'today_count': summary['today_count'],
            'unique_patient_count': summary['unique_patient_count'],
            'active_staff_count': summary['active_staff_count'],
            'current_local_time': summary['current_local_time'],
            'next_appointment': summary['next_appointment'],
            'average_daily_load': summary['average_daily_load'],
            'busiest_day_label': summary['busiest_day_label'],
            'busiest_day_count': summary['busiest_day_count'],
            'filter_badges': filter_badges,
            'has_active_filters': bool(filter_badges),
            'is_doctor': _is_doctor(request.user),
            'is_admin': _is_admin(request.user),
            'can_create': can_create,
            'can_update': _is_admin(request.user) or _is_doctor(request.user) or _is_frontdesk(request.user),
            'can_view_history': _is_admin(request.user) or _is_frontdesk(request.user) or _is_doctor(request.user),
            'walkin_form': walkin_form,
            'open_create_modal': open_create_modal,
            'plan_usage': plan_usage,
        },
    )


@login_required
def staff_appointment_edit(request, appointment_id: int):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    if not (_is_admin(request.user) or _is_doctor(request.user) or _is_frontdesk(request.user)):
        return HttpResponseForbidden('Only doctors, front desk, or admins can update appointments.')

    clinic = staff.clinic
    appointment = get_object_or_404(Appointment, pk=appointment_id, clinic=clinic)

    if _is_doctor(request.user) and not _is_admin(request.user):
        if appointment.staff_id != staff.id:
            return HttpResponseForbidden('Doctor access restricted to own appointments.')

    if _is_frontdesk(request.user) and not _is_admin(request.user):
        # Front desk cannot modify completed appointments.
        if appointment.status == Appointment.Status.COMPLETED:
            return HttpResponseForbidden('Front desk cannot modify completed appointments.')

    if request.method == 'POST':
        if _is_frontdesk(request.user) and not _is_admin(request.user):
            staff_qs = Staff.objects.filter(
                clinic=clinic,
                is_active=True,
                user__groups__name__in=['Doctor', 'Admin'],
            ).distinct()
            form = AppointmentFrontDeskUpdateForm(
                request.POST,
                instance=appointment,
                staff_qs=staff_qs,
            )
        else:
            form = AppointmentUpdateForm(request.POST, instance=appointment)
        if form.is_valid():
            old_status = appointment.status
            old_staff_id = appointment.staff_id
            old_start_at = appointment.start_at

            if _is_frontdesk(request.user) and not _is_admin(request.user):
                updated = form.save(commit=False)
                start_at = form.cleaned_data.get('start_at') or appointment.start_at
                if timezone.is_naive(start_at):
                    start_at = timezone.make_aware(start_at, ZoneInfo(clinic.timezone or 'UTC'))
                duration_minutes = (
                    appointment.appointment_type.duration_minutes
                    if appointment.appointment_type
                    else getattr(settings, 'APPOINTMENT_SLOT_MINUTES', 30)
                )
                updated.start_at = start_at
                updated.end_at = start_at + timedelta(minutes=duration_minutes)
            else:
                updated = form.save(commit=False)

            changes = []
            if old_status != updated.status:
                changes.append(f'status {old_status} -> {updated.status}')
            if old_staff_id != updated.staff_id:
                changes.append(f'staff {old_staff_id} -> {updated.staff_id}')
            if old_start_at != updated.start_at:
                changes.append('time changed')
            if updated.status == Appointment.Status.CANCELLED and updated.cancel_reason:
                changes.append('cancel_reason set')
            if changes:
                role = 'Admin' if _is_admin(request.user) else ('FrontDesk' if _is_frontdesk(request.user) else 'Doctor')
                updated._change_reason = f'{role} update: ' + ', '.join(changes)

            try:
                updated.save()
            except ValidationError as exc:
                if hasattr(exc, 'message_dict'):
                    for field, errors in exc.message_dict.items():
                        form.add_error(field, errors)
                else:
                    form.add_error('start_at', exc.messages)
                return render(
                    request,
                    'core/staff_appointment_edit.html',
                    {
                        'appointment': appointment,
                        'clinic': clinic,
                        'form': form,
                        'is_frontdesk': _is_frontdesk(request.user) and not _is_admin(request.user),
                    },
                )
            if changes:
                _notify_clinic_appointment_updated(appointment=updated, actor=request.user)
            messages.success(request, 'Appointment updated.')
            return redirect('staff-appointments')
    else:
        if _is_frontdesk(request.user) and not _is_admin(request.user):
            staff_qs = Staff.objects.filter(
                clinic=clinic,
                is_active=True,
                user__groups__name__in=['Doctor', 'Admin'],
            ).distinct()
            form = AppointmentFrontDeskUpdateForm(
                instance=appointment,
                staff_qs=staff_qs,
            )
            local_start = timezone.localtime(
                appointment.start_at,
                ZoneInfo(clinic.timezone or 'UTC'),
            )
            form.initial['start_at'] = local_start.replace(tzinfo=None)
        else:
            form = AppointmentUpdateForm(instance=appointment)

    return render(
        request,
        'core/staff_appointment_edit.html',
        {
            'appointment': appointment,
            'clinic': clinic,
            'form': form,
            'is_frontdesk': _is_frontdesk(request.user) and not _is_admin(request.user),
        },
    )


@login_required
def staff_appointment_create(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    if not (_is_admin(request.user) or _is_frontdesk(request.user)):
        return HttpResponseForbidden('Only Admin or Front Desk can create walk-in appointments.')

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    plan_usage = clinic_usage_summary(clinic)

    if request.method == 'POST':
        form = _build_walk_in_form(clinic=clinic, data=request.POST)
        if form.is_valid():
            if not clinic_can_accept_appointment(clinic, usage=plan_usage):
                form.add_error(
                    None,
                    _limit_reached_message(
                        item=plan_usage['appointments'],
                        resource_label='appointments',
                        action_label='creating more appointments',
                    ),
                )
            else:
                appointment = _save_walk_in_appointment(form, clinic, tz)
                if appointment:
                    _notify_clinic_appointment_created(appointment=appointment, actor=request.user)
                    messages.success(request, f'Appointment added: {_patient_label(appointment.patient)}.')
                    return redirect('staff-appointments')
    else:
        form = _build_walk_in_form(clinic=clinic)

    return render(
        request,
        'core/staff_appointment_create.html',
        {
            'clinic': clinic,
            'form': form,
            'plan_usage': plan_usage,
        },
    )


@login_required
def staff_appointment_history(request, appointment_id: int):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    if not (_is_admin(request.user) or _is_frontdesk(request.user) or _is_doctor(request.user)):
        return HttpResponseForbidden('Role cannot view appointment history.')

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    appointment = get_object_or_404(Appointment, pk=appointment_id, clinic=clinic)

    if _is_doctor(request.user) and not _is_admin(request.user):
        if appointment.staff_id != staff.id:
            return HttpResponseForbidden('Doctor access restricted to own appointments.')

    is_admin = _is_admin(request.user)
    is_frontdesk = _is_frontdesk(request.user) and not is_admin

    history = list(
        appointment.history.select_related('history_user', 'staff', 'patient')
        .order_by('-history_date')
    )
    if is_frontdesk:
        history = [item for item in history if item.status == Appointment.Status.CANCELLED]

    history_rows = []
    created_count = 0
    changed_count = 0
    deleted_count = 0
    for item in history:
        if item.history_type == '+':
            created_count += 1
            history_label = 'Created'
        elif item.history_type == '-':
            deleted_count += 1
            history_label = 'Deleted'
        else:
            changed_count += 1
            history_label = 'Changed'

        history_rows.append(
            {
                'item': item,
                'history_label': history_label,
                'history_date_local': timezone.localtime(item.history_date, tz),
                'start_at_local': timezone.localtime(item.start_at, tz) if item.start_at else None,
                'end_at_local': timezone.localtime(item.end_at, tz) if item.end_at else None,
            }
        )

    return render(
        request,
        'core/staff_appointment_history.html',
        {
            'clinic': clinic,
            'appointment': appointment,
            'history': history,
            'history_rows': history_rows,
            'history_total': len(history_rows),
            'created_count': created_count,
            'changed_count': changed_count,
            'deleted_count': deleted_count,
            'current_local_time': timezone.localtime(timezone.now(), tz),
            'limited_history': is_frontdesk,
        },
    )


@login_required
def staff_waitlist(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    if not (_is_admin(request.user) or _is_frontdesk(request.user)):
        return HttpResponseForbidden('Only Admin or Front Desk can review the waitlist.')

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    valid_statuses = {choice[0] for choice in WaitlistEntry.Status.choices}

    if request.method == 'POST':
        entry = get_object_or_404(WaitlistEntry, pk=request.POST.get('entry_id'), clinic=clinic)
        next_url = request.POST.get('next') or reverse('staff-waitlist')
        new_status = request.POST.get('status', '').strip()
        if new_status not in valid_statuses:
            messages.error(request, 'Choose a valid waitlist status.')
            return redirect(next_url)
        entry.status = new_status
        entry.save(update_fields=['status'])
        messages.success(request, f'Waitlist updated: {entry.first_name} {entry.last_name} is now {entry.get_status_display().lower()}.')
        return redirect(next_url)

    status_filter = request.GET.get('status', '').strip()
    appointment_type_filter = request.GET.get('type', '').strip()

    waitlist_qs = (
        WaitlistEntry.objects.filter(clinic=clinic)
        .select_related('patient', 'appointment_type')
        .order_by('status', 'preferred_start_date', 'created_at')
    )
    if status_filter in valid_statuses:
        waitlist_qs = waitlist_qs.filter(status=status_filter)
    else:
        status_filter = ''

    selected_type_id = ''
    if appointment_type_filter:
        try:
            selected_type_id = str(int(appointment_type_filter))
        except (TypeError, ValueError):
            selected_type_id = ''
    if selected_type_id:
        waitlist_qs = waitlist_qs.filter(appointment_type_id=selected_type_id)

    waitlist_entries = list(waitlist_qs)
    status_counts = {
        status_value: WaitlistEntry.objects.filter(clinic=clinic, status=status_value).count()
        for status_value, _ in WaitlistEntry.Status.choices
    }
    appointment_types = list(AppointmentType.objects.filter(clinic=clinic).order_by('name'))

    return render(
        request,
        'core/staff_waitlist.html',
        {
            'clinic': clinic,
            'waitlist_entries': waitlist_entries,
            'status_choices': WaitlistEntry.Status.choices,
            'status_filter': status_filter,
            'status_counts': status_counts,
            'appointment_types': appointment_types,
            'selected_type_id': selected_type_id,
            'current_local_time': timezone.localtime(timezone.now(), tz),
            'active_total': status_counts.get(WaitlistEntry.Status.ACTIVE, 0),
            'contacted_total': status_counts.get(WaitlistEntry.Status.CONTACTED, 0),
            'booked_total': status_counts.get(WaitlistEntry.Status.BOOKED, 0),
            'closed_total': status_counts.get(WaitlistEntry.Status.CLOSED, 0),
        },
    )


@login_required
def staff_patients(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    clinic = staff.clinic
    qs = Patient.objects.filter(clinic=clinic)

    if _is_doctor(request.user) and not _is_admin(request.user):
        patient_ids = Appointment.objects.filter(
            clinic=clinic,
            staff=staff,
        ).values_list('patient_id', flat=True)
        qs = qs.filter(id__in=patient_ids)

    patients = list(qs.order_by('last_name', 'first_name'))
    patient_ids = [patient.id for patient in patients]
    tz = ZoneInfo(clinic.timezone or 'UTC')
    appointments_qs = Appointment.objects.none()
    if patient_ids:
        appointments_qs = (
            Appointment.objects.filter(clinic=clinic, patient_id__in=patient_ids)
            .select_related('staff', 'appointment_type')
            .order_by('start_at')
        )
        if _is_doctor(request.user) and not _is_admin(request.user):
            appointments_qs = appointments_qs.filter(staff=staff)

    now = timezone.now()
    patient_map = {
        patient.id: {
            'patient': patient,
            'appointment_count': 0,
            'upcoming_count': 0,
            'next_appointment': None,
            'last_appointment': None,
        }
        for patient in patients
    }

    for appt in appointments_qs:
        row = patient_map.get(appt.patient_id)
        if not row:
            continue
        row['appointment_count'] += 1
        row['last_appointment'] = appt
        if appt.start_at >= now:
            row['upcoming_count'] += 1
            if row['next_appointment'] is None:
                row['next_appointment'] = appt

    patient_rows = list(patient_map.values())
    total_upcoming = sum(row['upcoming_count'] for row in patient_rows)
    patients_with_upcoming = sum(1 for row in patient_rows if row['upcoming_count'] > 0)
    for row in patient_rows:
        row['next_appointment_local'] = (
            timezone.localtime(row['next_appointment'].start_at, tz)
            if row['next_appointment']
            else None
        )
        row['last_appointment_local'] = (
            timezone.localtime(row['last_appointment'].start_at, tz)
            if row['last_appointment']
            else None
        )

    return render(
        request,
        'core/staff_patients.html',
        {
            'clinic': clinic,
            'patients': patients,
            'patient_rows': patient_rows,
            'total_patient_count': len(patient_rows),
            'patients_with_upcoming': patients_with_upcoming,
            'total_upcoming_appointments': total_upcoming,
            'current_local_time': timezone.localtime(now, tz),
            'can_edit': _is_admin(request.user) or _is_frontdesk(request.user),
        },
    )


@login_required
def staff_members(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    plan_usage = clinic_usage_summary(clinic)
    add_form = StaffMemberCreateForm(prefix='add')
    edit_form = StaffMemberUpdateForm(prefix='edit')
    open_modal = False
    open_edit_modal = False
    edit_action_url = ''

    if request.method == 'POST':
        add_form = StaffMemberCreateForm(request.POST, prefix='add')
        if add_form.is_valid():
            if not clinic_can_add_staff(clinic, usage=plan_usage):
                add_form.add_error(
                    None,
                    _limit_reached_message(
                        item=plan_usage['staff'],
                        resource_label='staff seats',
                        action_label='adding more staff',
                    ),
                )
            else:
                member = _save_staff_member_form(request, clinic, add_form)
                if member:
                    _notify_clinic_staff_change(
                        clinic=clinic,
                        member=member,
                        actor=request.user,
                        created=True,
                    )
                    messages.success(request, f'Staff added: {_user_label(member.user)}.')
                    return redirect('staff-members')
        open_modal = True

    return render(
        request,
        'core/staff_members.html',
        {
            'clinic': clinic,
            'add_form': add_form,
            'edit_form': edit_form,
            'open_modal': open_modal,
            'open_edit_modal': open_edit_modal,
            'edit_action_url': edit_action_url,
            'plan_usage': plan_usage,
            **_build_staff_members_context(clinic),
        },
    )


def _build_service_catalog_context(clinic: Clinic) -> dict:
    tz = ZoneInfo(clinic.timezone or 'UTC')
    now = timezone.now()
    appointment_types = list(AppointmentType.objects.filter(clinic=clinic).order_by('name'))
    appointments = list(
        Appointment.objects.filter(clinic=clinic, appointment_type__isnull=False)
        .select_related('appointment_type')
        .order_by('start_at')
    )

    stats_by_type: dict[int, dict] = {}
    total_upcoming_bookings = 0
    for appointment in appointments:
        appointment_type_id = appointment.appointment_type_id
        if not appointment_type_id:
            continue

        stats = stats_by_type.setdefault(
            appointment_type_id,
            {
                'appointment_count': 0,
                'completed_count': 0,
                'cancelled_count': 0,
                'upcoming_count': 0,
                'next_booking': None,
                'last_booking': None,
            },
        )
        stats['appointment_count'] += 1

        if appointment.status == Appointment.Status.COMPLETED:
            stats['completed_count'] += 1
        elif appointment.status == Appointment.Status.CANCELLED:
            stats['cancelled_count'] += 1

        if appointment.status == Appointment.Status.SCHEDULED and appointment.start_at >= now:
            stats['upcoming_count'] += 1
            total_upcoming_bookings += 1
            next_booking = stats['next_booking']
            if next_booking is None or appointment.start_at < next_booking.start_at:
                stats['next_booking'] = appointment

        last_booking = stats['last_booking']
        if last_booking is None or appointment.start_at > last_booking.start_at:
            stats['last_booking'] = appointment

    service_rows = []
    active_service_count = 0
    priced_service_count = 0
    total_duration = 0
    total_price_cents = 0
    services_with_upcoming = 0
    busiest_service_name = 'No bookings yet'
    busiest_service_count = 0
    next_service_name = ''
    next_service_booking = None

    for appointment_type in appointment_types:
        stats = stats_by_type.get(
            appointment_type.id,
            {
                'appointment_count': 0,
                'completed_count': 0,
                'cancelled_count': 0,
                'upcoming_count': 0,
                'next_booking': None,
                'last_booking': None,
            },
        )
        if appointment_type.is_active:
            active_service_count += 1
        if appointment_type.price_cents is not None:
            priced_service_count += 1
            total_price_cents += appointment_type.price_cents
            price_display = f"${appointment_type.price_cents / 100:.2f}"
            price_caption = 'Fixed service price'
        else:
            price_display = 'Flexible'
            price_caption = 'Price set manually when needed'

        total_duration += appointment_type.duration_minutes
        if stats['upcoming_count']:
            services_with_upcoming += 1
        if stats['appointment_count'] > busiest_service_count:
            busiest_service_count = stats['appointment_count']
            busiest_service_name = appointment_type.name

        next_booking = stats['next_booking']
        if next_booking and (next_service_booking is None or next_booking.start_at < next_service_booking.start_at):
            next_service_booking = next_booking
            next_service_name = appointment_type.name

        service_rows.append(
            {
                'type': appointment_type,
                'price_display': price_display,
                'price_caption': price_caption,
                'status_label': 'Active' if appointment_type.is_active else 'Inactive',
                'status_class': 'active' if appointment_type.is_active else 'inactive',
                'duration_label': f'{appointment_type.duration_minutes} min',
                'appointment_count': stats['appointment_count'],
                'completed_count': stats['completed_count'],
                'cancelled_count': stats['cancelled_count'],
                'upcoming_count': stats['upcoming_count'],
                'next_booking_local': timezone.localtime(next_booking.start_at, tz) if next_booking else None,
                'last_booking_local': timezone.localtime(stats['last_booking'].start_at, tz) if stats['last_booking'] else None,
            }
        )

    total_service_count = len(service_rows)
    average_duration = round(total_duration / total_service_count) if total_service_count else 0
    inactive_service_count = total_service_count - active_service_count
    average_price_display = (
        f"${(total_price_cents / priced_service_count) / 100:.2f}"
        if priced_service_count
        else 'Flexible'
    )

    return {
        'service_rows': service_rows,
        'total_service_count': total_service_count,
        'active_service_count': active_service_count,
        'inactive_service_count': inactive_service_count,
        'priced_service_count': priced_service_count,
        'average_duration': average_duration,
        'average_price_display': average_price_display,
        'services_with_upcoming': services_with_upcoming,
        'total_upcoming_bookings': total_upcoming_bookings,
        'busiest_service_name': busiest_service_name,
        'busiest_service_count': busiest_service_count,
        'next_service_name': next_service_name,
        'next_service_booking_local': timezone.localtime(next_service_booking.start_at, tz) if next_service_booking else None,
        'current_local_time': timezone.localtime(now, tz),
    }


@login_required
def appointment_types(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    plan_usage = clinic_usage_summary(clinic)
    add_form = AppointmentTypeForm(prefix='add', clinic=clinic)
    edit_form = AppointmentTypeForm(prefix='edit', clinic=clinic)
    open_modal = False
    open_edit_modal = False
    edit_action_url = ''

    if request.method == 'POST':
        add_form = AppointmentTypeForm(request.POST, prefix='add', clinic=clinic)
        if add_form.is_valid():
            if not clinic_can_add_service(clinic, usage=plan_usage):
                add_form.add_error(
                    None,
                    _limit_reached_message(
                        item=plan_usage['services'],
                        resource_label='services',
                        action_label='adding more services',
                    ),
                )
            else:
                price_cents = add_form.cleaned_data.get('price_cents')
                appointment_type = AppointmentType.objects.create(
                    clinic=clinic,
                    name=add_form.cleaned_data['name'],
                    duration_minutes=add_form.cleaned_data['duration_minutes'],
                    price_cents=price_cents if price_cents is not None else None,
                    is_active=bool(add_form.cleaned_data.get('is_active')),
                )
                _notify_clinic_service_change(
                    clinic=clinic,
                    appointment_type=appointment_type,
                    actor=request.user,
                    created=True,
                )
                messages.success(request, f'Service added: {appointment_type.name}.')
                return redirect('appointment-types')
        open_modal = True
    catalog_context = _build_service_catalog_context(clinic)

    return render(
        request,
        'core/appointment_types.html',
        {
            'clinic': clinic,
            'add_form': add_form,
            'edit_form': edit_form,
            'open_modal': open_modal,
            'open_edit_modal': open_edit_modal,
            'edit_action_url': edit_action_url,
            'plan_usage': plan_usage,
            **catalog_context,
        },
    )


@login_required
def appointment_type_create(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    catalog_context = _build_service_catalog_context(clinic)
    plan_usage = clinic_usage_summary(clinic)
    if request.method == 'POST':
        form = AppointmentTypeForm(request.POST, clinic=clinic)
        if form.is_valid():
            if not clinic_can_add_service(clinic, usage=plan_usage):
                form.add_error(
                    None,
                    _limit_reached_message(
                        item=plan_usage['services'],
                        resource_label='services',
                        action_label='adding more services',
                    ),
                )
            else:
                price_cents = form.cleaned_data.get('price_cents')
                appointment_type = AppointmentType.objects.create(
                    clinic=clinic,
                    name=form.cleaned_data['name'],
                    duration_minutes=form.cleaned_data['duration_minutes'],
                    price_cents=price_cents if price_cents is not None else None,
                    is_active=bool(form.cleaned_data.get('is_active')),
                )
                _notify_clinic_service_change(
                    clinic=clinic,
                    appointment_type=appointment_type,
                    actor=request.user,
                    created=True,
                )
                messages.success(request, f'Service added: {appointment_type.name}.')
                return redirect('appointment-types')
    else:
        form = AppointmentTypeForm(clinic=clinic)

    return render(
        request,
        'core/appointment_type_form.html',
        {
            'clinic': clinic,
            'form': form,
            'title': 'Add service',
            'submit_label': 'Create service',
            'plan_usage': plan_usage,
            **catalog_context,
        },
    )


@login_required
def appointment_type_edit(request, type_id: int):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    appt_type = get_object_or_404(AppointmentType, pk=type_id, clinic=clinic)
    catalog_context = _build_service_catalog_context(clinic)

    if request.method == 'POST':
        form = AppointmentTypeForm(request.POST, prefix='edit', clinic=clinic, instance=appt_type)
        if form.is_valid():
            appt_type.name = form.cleaned_data['name']
            appt_type.duration_minutes = form.cleaned_data['duration_minutes']
            price_cents = form.cleaned_data.get('price_cents')
            appt_type.price_cents = price_cents if price_cents is not None else None
            appt_type.is_active = bool(form.cleaned_data.get('is_active'))
            appt_type.save(update_fields=['name', 'duration_minutes', 'price_cents', 'is_active'])
            _notify_clinic_service_change(
                clinic=clinic,
                appointment_type=appt_type,
                actor=request.user,
                created=False,
            )
            messages.success(request, f'Service updated: {appt_type.name}.')
            return redirect('appointment-types')
        return render(
            request,
            'core/appointment_types.html',
            {
                'clinic': clinic,
                'add_form': AppointmentTypeForm(prefix='add', clinic=clinic),
                'edit_form': form,
                'open_modal': False,
                'open_edit_modal': True,
                'edit_action_url': reverse('appointment-type-edit', args=[appt_type.id]),
                **catalog_context,
            },
        )
    else:
        form = AppointmentTypeForm(clinic=clinic, instance=appt_type)

    return render(
        request,
        'core/appointment_type_form.html',
        {
            'clinic': clinic,
            'form': form,
            'title': 'Edit service',
            'submit_label': 'Save changes',
            'appointment_type': appt_type,
            **catalog_context,
        },
    )


@login_required
def staff_member_create(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    plan_usage = clinic_usage_summary(clinic)
    if request.method == 'POST':
        form = StaffMemberCreateForm(request.POST)
        if form.is_valid():
            if not clinic_can_add_staff(clinic, usage=plan_usage):
                form.add_error(
                    None,
                    _limit_reached_message(
                        item=plan_usage['staff'],
                        resource_label='staff seats',
                        action_label='adding more staff',
                    ),
                )
            else:
                member = _save_staff_member_form(request, clinic, form)
                if member:
                    _notify_clinic_staff_change(
                        clinic=clinic,
                        member=member,
                        actor=request.user,
                        created=True,
                    )
                    messages.success(request, f'Staff added: {_user_label(member.user)}.')
                    return redirect('staff-members')
    else:
        form = StaffMemberCreateForm()

    return render(
        request,
        'core/staff_member_form.html',
        {
            'clinic': clinic,
            'form': form,
            'title': 'Add staff',
            'submit_label': 'Create staff',
            'plan_usage': plan_usage,
        },
    )


@login_required
def staff_member_edit(request, staff_id: int):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    member = get_object_or_404(Staff, pk=staff_id, clinic=clinic)
    user = member.user

    if request.method == 'POST':
        from_modal = request.POST.get('origin') == 'modal'
        form_kwargs = {'prefix': 'edit'} if from_modal else {}
        form = StaffMemberUpdateForm(request.POST, **form_kwargs)
        if form.is_valid():
            saved_member = _save_staff_member_form(request, clinic, form, member=member)
            if saved_member:
                _notify_clinic_staff_change(
                    clinic=clinic,
                    member=saved_member,
                    actor=request.user,
                    created=False,
                )
                messages.success(request, f'Staff updated: {_user_label(saved_member.user)}.')
                return redirect('staff-members')
        if from_modal:
            return render(
                request,
                'core/staff_members.html',
                {
                    'clinic': clinic,
                    'add_form': StaffMemberCreateForm(prefix='add'),
                    'edit_form': form,
                    'open_modal': False,
                    'open_edit_modal': True,
                    'edit_action_url': reverse('staff-member-edit', args=[member.id]),
                    **_build_staff_members_context(clinic),
                },
            )
    else:
        form = StaffMemberUpdateForm(initial=_staff_member_initial(user))

    return render(
        request,
        'core/staff_member_form.html',
        {
            'clinic': clinic,
            'form': form,
            'title': 'Edit staff',
            'submit_label': 'Save changes',
            'staff_member': member,
        },
    )


@login_required
def staff_patient_edit(request, patient_id: int):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
    patient = get_object_or_404(Patient, pk=patient_id, clinic=clinic)

    if _is_doctor(request.user) and not _is_admin(request.user):
        has_relationship = Appointment.objects.filter(
            clinic=clinic,
            staff=staff,
            patient=patient,
        ).exists()
        if not has_relationship:
            return HttpResponseForbidden('Doctor access restricted to assigned patients.')

    can_edit = _is_admin(request.user) or _is_frontdesk(request.user)
    if request.method == 'POST':
        if not can_edit:
            return HttpResponseForbidden('Role cannot edit patients.')
        form = PatientUpdateForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            messages.success(request, 'Patient updated.')
            return redirect('staff-patients')
    else:
        form = PatientUpdateForm(instance=patient)
        if not can_edit:
            for field in form.fields.values():
                field.disabled = True

    appointments_qs = Appointment.objects.filter(
        clinic=clinic,
        patient=patient,
    ).select_related('staff', 'appointment_type').order_by('-start_at')
    if _is_doctor(request.user) and not _is_admin(request.user):
        appointments_qs = appointments_qs.filter(staff=staff)

    appointments = list(appointments_qs)
    appointment_rows = []
    now = timezone.now()
    upcoming_count = 0
    completed_count = 0
    cancelled_count = 0
    for appt in appointments:
        if appt.start_at >= now:
            upcoming_count += 1
        if appt.status == Appointment.Status.COMPLETED:
            completed_count += 1
        elif appt.status == Appointment.Status.CANCELLED:
            cancelled_count += 1
        appointment_rows.append(
            {
                'appointment': appt,
                'local_start': timezone.localtime(appt.start_at, tz),
                'local_end': timezone.localtime(appt.end_at, tz),
            }
        )

    return render(
        request,
        'core/staff_patient_edit.html',
        {
            'clinic': clinic,
            'patient': patient,
            'form': form,
            'appointments': appointments,
            'appointment_rows': appointment_rows,
            'appointment_count': len(appointment_rows),
            'upcoming_count': upcoming_count,
            'completed_count': completed_count,
            'cancelled_count': cancelled_count,
            'current_local_time': timezone.localtime(now, tz),
            'can_edit': can_edit,
            'can_update_appointments': _is_admin(request.user) or _is_doctor(request.user) or _is_frontdesk(request.user),
            'can_view_history': _is_admin(request.user) or _is_frontdesk(request.user) or _is_doctor(request.user),
        },
    )


@require_POST
def signup_activate(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON payload.')

    clinic_id = payload.get('clinic_id')
    plan_id = payload.get('plan_id')
    subscription_id = payload.get('subscription_id')
    if not clinic_id or not plan_id:
        return HttpResponseBadRequest('clinic_id and plan_id are required.')

    try:
        clinic_id = int(clinic_id)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('Invalid clinic_id.')

    clinic = Clinic.objects.filter(id=clinic_id).first()
    if not clinic:
        return HttpResponseBadRequest('Clinic not found.')

    if request.session.get('signup_clinic_id') != clinic_id:
        if not request.user.is_authenticated:
            return HttpResponseForbidden('Signup session mismatch.')
        try:
            staff = request.user.staff
        except Staff.DoesNotExist:
            return HttpResponseForbidden('Signup session mismatch.')
        if staff.clinic_id != clinic_id:
            return HttpResponseForbidden('Signup session mismatch.')
        if not (request.user.is_superuser or request.user.groups.filter(name='Admin').exists()):
            return HttpResponseForbidden('Signup session mismatch.')

    plan = get_object_or_404(Plan, pk=plan_id, is_active=True)
    if not plan.is_free and not subscription_id:
        return HttpResponseBadRequest('subscription_id is required for paid plans.')

    previous_subscription = get_current_subscription(clinic)
    previous_status = previous_subscription.status if previous_subscription else None
    previous_plan_id = previous_subscription.plan_id if previous_subscription else None

    subscription = _activate_selected_plan(
        clinic=clinic,
        plan=plan,
        subscription_id=subscription_id,
        last_event_type='FREE_ACTIVATED' if plan.is_free else 'CLIENT_APPROVED',
        sync_event_type='CLIENT_SYNCED',
    )

    if (
        previous_subscription is None
        or previous_status != subscription.status
        or previous_plan_id != subscription.plan_id
    ):
        owner_user = _get_clinic_owner_user(clinic)
        if owner_user is not None:
            _send_platform_plan_activation_alert(
                request=request,
                clinic=clinic,
                owner_user=owner_user,
                plan=plan,
                subscription=subscription,
            )

    return JsonResponse(
        {
            'ok': True,
            'subscription_id': subscription.paypal_subscription_id,
            'is_free': plan.is_free,
            'status': subscription.status,
            'plan_name': plan.name,
        }
    )


def _require_admin_staff(request):
    try:
        staff = request.user.staff
    except Staff.DoesNotExist:
        return None, HttpResponseForbidden('Staff access required.')

    if not (request.user.is_superuser or request.user.groups.filter(name='Admin').exists()):
        return None, HttpResponseForbidden('Admin role required.')

    return staff, None


@login_required
def billing_view(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    plans = Plan.objects.filter(is_active=True).order_by('price_cents')
    current_subscription = get_current_subscription(clinic)
    plan_usage = clinic_usage_summary(clinic)

    return render(
        request,
        'core/billing.html',
        {
            'clinic': clinic,
            'plans': plans,
            'current_subscription': current_subscription,
            'plan_usage': plan_usage,
            'paypal_client_id': settings.PAYPAL_CLIENT_ID,
            'paypal_sdk_url': settings.PAYPAL_SDK_URL,
            'current_local_time': timezone.localtime(
                timezone.now(),
                ZoneInfo(clinic.timezone or 'UTC'),
            ),
        },
    )


@login_required
@require_POST
def billing_activate(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON payload.')

    plan_id = payload.get('plan_id')
    subscription_id = payload.get('subscription_id')
    if not plan_id:
        return HttpResponseBadRequest('plan_id is required.')

    plan = get_object_or_404(Plan, pk=plan_id, is_active=True)
    clinic = staff.clinic
    previous_subscription = get_current_subscription(clinic)
    previous_status = previous_subscription.status if previous_subscription else None
    previous_plan_id = previous_subscription.plan_id if previous_subscription else None

    if not plan.is_free and not subscription_id:
        return HttpResponseBadRequest('subscription_id is required for paid plans.')

    subscription = _activate_selected_plan(
        clinic=clinic,
        plan=plan,
        subscription_id=subscription_id,
        last_event_type='FREE_ACTIVATED' if plan.is_free else 'CLIENT_APPROVED',
        sync_event_type='CLIENT_SYNCED',
    )

    if (
        previous_subscription is None
        or previous_status != subscription.status
        or previous_plan_id != subscription.plan_id
    ):
        notification_title = (
            'Subscription activated'
            if subscription.status == ClinicSubscription.Status.ACTIVE
            else 'Subscription activation recorded'
        )
        _notify_clinic_subscription_change(
            clinic=clinic,
            subscription=subscription,
            actor=request.user,
            title=notification_title,
            created=subscription.status == ClinicSubscription.Status.ACTIVE,
        )

    return JsonResponse(
        {
            'ok': True,
            'subscription_id': subscription.paypal_subscription_id,
            'is_free': plan.is_free,
            'status': subscription.status,
            'plan_name': plan.name,
        }
    )


@login_required
@require_POST
def billing_sync(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    subscription = get_current_subscription(clinic)
    if not subscription:
        return JsonResponse({'ok': False, 'error': 'No subscription found.'}, status=400)
    if subscription.plan.is_free or subscription.paypal_subscription_id.startswith('LOCAL-'):
        return JsonResponse(
            {'ok': False, 'error': 'Free plans do not sync with PayPal.'},
            status=400,
        )

    previous_status = subscription.status
    try:
        _sync_subscription_from_paypal(subscription, last_event_type='MANUAL_SYNC')
    except PayPalError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    if previous_status != subscription.status:
        _notify_clinic_subscription_change(
            clinic=clinic,
            subscription=subscription,
            actor=request.user,
            title='Subscription synced',
            created=subscription.status == ClinicSubscription.Status.ACTIVE,
        )

    return JsonResponse({'ok': True, 'status': subscription.status})


@csrf_exempt
@require_POST
def paypal_webhook(request):
    raw_body = request.body
    try:
        event = json.loads(raw_body.decode('utf-8'))
    except json.JSONDecodeError:
        return HttpResponseBadRequest('Invalid JSON payload.')

    try:
        is_valid = verify_webhook_signature(request.headers, event)
    except PayPalError:
        return HttpResponseBadRequest('Webhook verification failed.')

    if not is_valid:
        return HttpResponseBadRequest('Invalid webhook signature.')

    event_type = event.get('event_type') or ''
    resource = event.get('resource', {}) or {}

    if not event_type.startswith('BILLING.SUBSCRIPTION'):
        webhook_event, _ = _build_paypal_webhook_event(event, raw_body, subscription_id=None)
        if webhook_event.status in {
            PayPalWebhookEvent.ProcessingStatus.PROCESSED,
            PayPalWebhookEvent.ProcessingStatus.IGNORED,
        }:
            return JsonResponse({'ok': True, 'duplicate': True, 'ignored': True})
        _finalize_paypal_event(
            webhook_event,
            status=PayPalWebhookEvent.ProcessingStatus.IGNORED,
            summary='Ignored non-subscription webhook.',
        )
        return JsonResponse({'ok': True, 'ignored': True})

    subscription_id = (
        resource.get('id')
        or resource.get('subscription_id')
        or resource.get('billing_agreement_id')
    )
    if not subscription_id:
        logger.warning('PayPal webhook missing subscription id for %s', event_type)
        return JsonResponse({'ok': True, 'ignored': True})

    webhook_event, created = _build_paypal_webhook_event(event, raw_body, subscription_id=subscription_id)
    if not created:
        if webhook_event.status in {
            PayPalWebhookEvent.ProcessingStatus.PROCESSED,
            PayPalWebhookEvent.ProcessingStatus.IGNORED,
        }:
            return JsonResponse({'ok': True, 'duplicate': True, 'status': webhook_event.status})
        webhook_event.event_type = event_type
        webhook_event.resource_id = subscription_id
        webhook_event.summary = event.get('summary') or ''
        webhook_event.payload = event
        webhook_event.error_message = ''
        webhook_event.status = PayPalWebhookEvent.ProcessingStatus.RECEIVED
        webhook_event.processed_at = None
        webhook_event.save(update_fields=[
            'event_type',
            'resource_id',
            'summary',
            'payload',
            'error_message',
            'status',
            'processed_at',
        ])

    plan_id = resource.get('plan_id')
    plan = Plan.objects.filter(paypal_plan_id=plan_id).first() if plan_id else None
    started_at = parse_paypal_datetime(resource.get('start_time'))
    billing_info = resource.get('billing_info') or {}
    next_billing = parse_paypal_datetime(billing_info.get('next_billing_time')) if 'next_billing_time' in billing_info else _UNSET

    try:
        with transaction.atomic():
            subscription_was_created = False
            previous_status = None
            subscription = (
                ClinicSubscription.objects.select_for_update()
                .filter(paypal_subscription_id=subscription_id)
                .first()
            )
            if not subscription:
                subscription_was_created = True
                clinic = None
                clinic_id = _clinic_id_from_custom_id(resource.get('custom_id'))
                if clinic_id:
                    clinic = Clinic.objects.filter(id=clinic_id).first()

                if not clinic or not plan:
                    logger.warning('PayPal webhook could not map subscription %s', subscription_id)
                    _finalize_paypal_event(
                        webhook_event,
                        status=PayPalWebhookEvent.ProcessingStatus.IGNORED,
                        summary=f'Ignored {event_type}: subscription could not be mapped.',
                    )
                    return JsonResponse({'ok': True, 'ignored': True})

                subscription = ClinicSubscription.objects.create(
                    clinic=clinic,
                    plan=plan,
                    paypal_subscription_id=subscription_id,
                    status=map_paypal_status(resource.get('status')),
                    started_at=started_at,
                    current_period_end=None if next_billing is _UNSET else next_billing,
                    last_event_type=event_type,
                )
            else:
                previous_status = subscription.status
                _apply_subscription_state(
                    subscription,
                    plan=plan if plan else _UNSET,
                    raw_status=resource.get('status') or _UNSET,
                    started_at=started_at or _UNSET,
                    current_period_end=next_billing,
                    last_event_type=event_type,
                )

            _finalize_paypal_event(
                webhook_event,
                status=PayPalWebhookEvent.ProcessingStatus.PROCESSED,
                summary=f'Processed {event_type} for {subscription_id}.',
                subscription=subscription,
            )
            should_notify = subscription_was_created or previous_status != subscription.status
            notification_title = (
                'Subscription activated'
                if subscription.status == ClinicSubscription.Status.ACTIVE
                else 'Subscription status changed'
            )
            if should_notify:
                transaction.on_commit(
                    lambda clinic=subscription.clinic,
                    subscription=subscription,
                    title=notification_title: _notify_clinic_subscription_change(
                        clinic=clinic,
                        subscription=subscription,
                        title=title,
                        created=subscription.status == ClinicSubscription.Status.ACTIVE,
                    )
                )
    except Exception as exc:
        logger.exception('Failed to process PayPal webhook %s', event_type)
        _finalize_paypal_event(
            webhook_event,
            status=PayPalWebhookEvent.ProcessingStatus.FAILED,
            summary=f'Failed {event_type} for {subscription_id}.',
            error_message=str(exc),
        )
        return JsonResponse({'ok': False, 'error': 'Webhook processing failed.'}, status=500)

    return JsonResponse({'ok': True, 'status': subscription.status})
