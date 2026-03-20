from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import logging
import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .booking import build_available_slots, parse_slot_value
from .forms import (
    AppointmentLookupForm,
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
)
from .models import (
    Appointment,
    AppointmentType,
    Clinic,
    ClinicSubscription,
    Patient,
    Plan,
    Staff,
)
from .paypal import PayPalError, get_subscription, verify_webhook_signature
from .subscriptions import clinic_has_active_subscription, map_paypal_status, parse_paypal_datetime

User = get_user_model()

ALLOWED_GROUPS = {'Admin', 'Doctor', 'Nurse', 'FrontDesk'}
logger = logging.getLogger(__name__)


class ClinicLoginView(LoginView):
    authentication_form = ClinicAuthenticationForm
    template_name = 'registration/login.html'


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
    subject = render_to_string('core/email_verify_subject.txt', context).strip()
    message = render_to_string('core/email_verify.txt', context)
    html_message = render_to_string('core/email_verify.html', context)
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        html_message=html_message,
        fail_silently=True,
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
        current_subscription = (
            ClinicSubscription.objects.filter(clinic=clinic)
            .select_related('plan')
            .order_by('-created_at')
            .first()
        )
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

        current_subscription = (
            ClinicSubscription.objects.filter(clinic=clinic)
            .select_related('plan')
            .order_by('-created_at')
            .first()
        )
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
            'is_admin': is_admin,
        }
        return render(request, 'core/dashboard.html', context)


def clinic_booking(request, clinic_id: int):
    clinic = get_object_or_404(Clinic, pk=clinic_id, is_active=True)
    return _clinic_booking(request, clinic)


def clinic_booking_slug(request, clinic_slug: str):
    clinic = get_object_or_404(Clinic, slug=clinic_slug, is_active=True)
    return _clinic_booking(request, clinic)


def _clinic_booking(request, clinic: Clinic):
    if settings.ENFORCE_SUBSCRIPTION and not clinic_has_active_subscription(clinic):
        return render(request, 'core/subscription_required.html', {'clinic': clinic})
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

    selected_type_id = request.GET.get('type') or request.POST.get('appointment_type_id')
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

    if request.method == 'POST':
        form = BookingForm(
            request.POST,
            slot_choices=slot_choices,
            appointment_type_id=selected_type.id if selected_type else None,
        )
        if form.is_valid():
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
                    end_at_local = timezone.localtime(end_at, clinic_tz)
                    email = form.cleaned_data['email'].strip().lower()
                    patient = None

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

                    appointment = Appointment(
                        clinic=clinic,
                        appointment_type=selected_type,
                        staff=staff,
                        patient=patient,
                        start_at=start_at,
                        end_at=end_at,
                        notes=form.cleaned_data.get('notes'),
                    )
                    try:
                        appointment.save()
                    except ValidationError:
                        patient.delete()
                        form.add_error('slot', 'That time was just booked. Please choose another slot.')
                    else:
                        if getattr(settings, 'SEND_BOOKING_CONFIRMATION', True) and patient.email:
                            send_mail(
                                f'Appointment confirmed - {clinic.name}',
                                (
                                    f'Hello {patient.first_name},\n\n'
                                    f'Your appointment at {clinic.name} is confirmed.\n'
                                    f'Time: {start_at_local:%b %d, %Y %I:%M %p} - {end_at_local:%I:%M %p} ({clinic.timezone})\n'
                                    f'Staff: {staff}\n'
                                    f'Confirmation code: {appointment.confirmation_code}\n\n'
                                    'Thank you.'
                                ),
                                settings.DEFAULT_FROM_EMAIL,
                                [patient.email],
                                fail_silently=True,
                            )
                        messages.success(request, 'Your appointment is booked.')
                        return render(
                            request,
                            'core/booking_success.html',
                            {
                                'clinic': clinic,
                                'appointment': appointment,
                                'appointment_local': start_at_local,
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
            'slot_count': len(slot_choices),
        },
    )


def appointment_lookup(request):
    appointment = None
    appointment_local = None
    error_message = None

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

    success = False
    if request.method == 'POST':
        form = AvatarUploadForm(request.POST, request.FILES)
        if form.is_valid() and profile is not None:
            avatar = form.cleaned_data.get('avatar')
            if avatar:
                profile.avatar = avatar
                profile.save(update_fields=['avatar'])
                success = True
    else:
        form = AvatarUploadForm()

    groups = list(request.user.groups.values_list('name', flat=True))
    tz = ZoneInfo(clinic.timezone or 'UTC') if clinic else timezone.get_current_timezone()
    return render(
        request,
        'core/settings.html',
        {
            'clinic': clinic,
            'groups': groups,
            'avatar_form': form,
            'avatar_saved': success,
            'avatar_url': avatar_url,
            'profile_type': profile_type,
            'current_local_time': timezone.localtime(timezone.now(), tz),
        },
    )


def clinic_signup(request):
    plans = Plan.objects.filter(is_active=True).order_by('price_cents')
    clinic = None
    clinic_id = request.session.get('signup_clinic_id')
    if clinic_id:
        clinic = Clinic.objects.filter(id=clinic_id).first()

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

                request.session['signup_clinic_id'] = clinic.id
                _send_verification_email(request, user, clinic=clinic)
                return render(
                    request,
                    'core/clinic_signup.html',
                    {
                        'form': ClinicSignupForm(),
                        'clinic': clinic,
                        'plans': plans,
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
                    Patient.objects.create(
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
                Patient.objects.create(
                    user=user,
                    clinic=clinic,
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    email=email,
                    phone=form.cleaned_data['phone'],
                    dob=form.cleaned_data.get('dob'),
                )
                _send_verification_email(request, user, clinic=clinic)
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
def staff_appointments(request):
    staff, error = _require_staff_portal(request)
    if error:
        return error

    clinic = staff.clinic
    tz = ZoneInfo(clinic.timezone or 'UTC')
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
            'can_create': _is_admin(request.user) or _is_frontdesk(request.user),
            'can_update': _is_admin(request.user) or _is_doctor(request.user) or _is_frontdesk(request.user),
            'can_view_history': _is_admin(request.user) or _is_frontdesk(request.user) or _is_doctor(request.user),
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
    staff_qs = Staff.objects.filter(clinic=clinic, is_active=True).select_related('user')
    appointment_type_qs = AppointmentType.objects.filter(clinic=clinic, is_active=True)

    if request.method == 'POST':
        form = WalkInAppointmentForm(
            request.POST,
            staff_qs=staff_qs,
            appointment_type_qs=appointment_type_qs,
        )
        if form.is_valid():
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
            else:
                messages.success(request, 'Walk-in appointment created.')
                return redirect('staff-appointments')
    else:
        form = WalkInAppointmentForm(
            staff_qs=staff_qs,
            appointment_type_qs=appointment_type_qs,
        )

    return render(
        request,
        'core/staff_appointment_create.html',
        {
            'clinic': clinic,
            'form': form,
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

    return render(
        request,
        'core/staff_members.html',
        {
            'clinic': clinic,
            'staff_rows': staff_rows,
            'total_staff_count': len(staff_rows),
            'active_staff_count': active_count,
            'inactive_staff_count': inactive_count,
            'role_counts': sorted(role_counts.items()),
            'current_local_time': timezone.localtime(now, tz),
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
    add_form = AppointmentTypeForm(prefix='add', clinic=clinic)
    edit_form = AppointmentTypeForm(prefix='edit', clinic=clinic)
    open_modal = False
    open_edit_modal = False
    edit_action_url = ''

    if request.method == 'POST':
        add_form = AppointmentTypeForm(request.POST, prefix='add', clinic=clinic)
        if add_form.is_valid():
            price_cents = add_form.cleaned_data.get('price_cents')
            appointment_type = AppointmentType.objects.create(
                clinic=clinic,
                name=add_form.cleaned_data['name'],
                duration_minutes=add_form.cleaned_data['duration_minutes'],
                price_cents=price_cents if price_cents is not None else None,
                is_active=bool(add_form.cleaned_data.get('is_active')),
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
    if request.method == 'POST':
        form = AppointmentTypeForm(request.POST, clinic=clinic)
        if form.is_valid():
            price_cents = form.cleaned_data.get('price_cents')
            appointment_type = AppointmentType.objects.create(
                clinic=clinic,
                name=form.cleaned_data['name'],
                duration_minutes=form.cleaned_data['duration_minutes'],
                price_cents=price_cents if price_cents is not None else None,
                is_active=bool(form.cleaned_data.get('is_active')),
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
    if request.method == 'POST':
        form = StaffMemberCreateForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
                form.add_error('email', 'An account with this email already exists.')
            else:
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
                Staff.objects.create(
                    user=user,
                    clinic=clinic,
                    is_active=is_active,
                )
                role = form.cleaned_data['role']
                group = Group.objects.filter(name=role).first()
                if group:
                    user.groups.add(group)
                if not is_active:
                    _send_verification_email(request, user, clinic=clinic)
                messages.success(request, f'Staff added: {_user_label(user)}.')
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
    current_role = _staff_role_for_user(user) or 'Doctor'

    if request.method == 'POST':
        form = StaffMemberUpdateForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            if email.lower() != (user.email or user.username).lower():
                if (
                    User.objects.filter(username__iexact=email).exclude(pk=user.pk).exists()
                    or User.objects.filter(email__iexact=email).exclude(pk=user.pk).exists()
                ):
                    form.add_error('email', 'An account with this email already exists.')
            if not form.errors:
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
                new_role = form.cleaned_data['role']
                group = Group.objects.filter(name=new_role).first()
                if group:
                    user.groups.add(group)

                if not user.is_active:
                    _send_verification_email(request, user, clinic=clinic)
                messages.success(request, f'Staff updated: {_user_label(user)}.')
                return redirect('staff-members')
    else:
        form = StaffMemberUpdateForm(
            initial={
                'email': user.email or user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'role': current_role,
                'is_active': user.is_active,
            }
        )

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
    if not clinic_id or not plan_id or not subscription_id:
        return HttpResponseBadRequest('clinic_id, plan_id and subscription_id are required.')

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
    subscription, _ = ClinicSubscription.objects.update_or_create(
        paypal_subscription_id=subscription_id,
        defaults={
            'clinic': clinic,
            'plan': plan,
            'status': ClinicSubscription.Status.PENDING,
            'last_event_type': 'CLIENT_APPROVED',
        },
    )
    try:
        details = get_subscription(subscription_id)
    except PayPalError:
        details = None

    if details:
        subscription.status = map_paypal_status(details.get('status'))
        subscription.started_at = (
            parse_paypal_datetime(details.get('start_time')) or subscription.started_at
        )
        billing_info = details.get('billing_info') or {}
        subscription.current_period_end = parse_paypal_datetime(billing_info.get('next_billing_time'))
        subscription.last_event_type = 'CLIENT_SYNCED'
        subscription.save(update_fields=[
            'status',
            'started_at',
            'current_period_end',
            'last_event_type',
        ])

    return JsonResponse({'ok': True, 'subscription_id': subscription.paypal_subscription_id})


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
    current_subscription = (
        ClinicSubscription.objects.filter(clinic=clinic)
        .order_by('-created_at')
        .select_related('plan')
        .first()
    )

    return render(
        request,
        'core/billing.html',
        {
            'clinic': clinic,
            'plans': plans,
            'current_subscription': current_subscription,
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
    if not plan_id or not subscription_id:
        return HttpResponseBadRequest('plan_id and subscription_id are required.')

    plan = get_object_or_404(Plan, pk=plan_id, is_active=True)
    clinic = staff.clinic

    subscription, _ = ClinicSubscription.objects.update_or_create(
        paypal_subscription_id=subscription_id,
        defaults={
            'clinic': clinic,
            'plan': plan,
            'status': ClinicSubscription.Status.PENDING,
            'last_event_type': 'CLIENT_APPROVED',
        },
    )
    try:
        details = get_subscription(subscription_id)
    except PayPalError:
        details = None

    if details:
        subscription.status = map_paypal_status(details.get('status'))
        subscription.started_at = (
            parse_paypal_datetime(details.get('start_time')) or subscription.started_at
        )
        billing_info = details.get('billing_info') or {}
        subscription.current_period_end = parse_paypal_datetime(billing_info.get('next_billing_time'))
        subscription.last_event_type = 'CLIENT_SYNCED'
        subscription.save(update_fields=[
            'status',
            'started_at',
            'current_period_end',
            'last_event_type',
        ])

    return JsonResponse({'ok': True, 'subscription_id': subscription.paypal_subscription_id})


@login_required
@require_POST
def billing_sync(request):
    staff, error = _require_admin_staff(request)
    if error:
        return error

    clinic = staff.clinic
    subscription = (
        ClinicSubscription.objects.filter(clinic=clinic)
        .select_related('plan')
        .order_by('-created_at')
        .first()
    )
    if not subscription:
        return JsonResponse({'ok': False, 'error': 'No subscription found.'}, status=400)

    try:
        details = get_subscription(subscription.paypal_subscription_id)
    except PayPalError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    subscription.status = map_paypal_status(details.get('status'))
    subscription.started_at = (
        parse_paypal_datetime(details.get('start_time')) or subscription.started_at
    )
    billing_info = details.get('billing_info') or {}
    subscription.current_period_end = parse_paypal_datetime(billing_info.get('next_billing_time'))
    subscription.last_event_type = 'MANUAL_SYNC'
    subscription.save(update_fields=[
        'status',
        'started_at',
        'current_period_end',
        'last_event_type',
    ])

    return JsonResponse({'ok': True, 'status': subscription.status})


@csrf_exempt
@require_POST
def paypal_webhook(request):
    try:
        event = json.loads(request.body.decode('utf-8'))
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
        return JsonResponse({'ok': True, 'ignored': True})

    subscription_id = (
        resource.get('id')
        or resource.get('subscription_id')
        or resource.get('billing_agreement_id')
    )
    if not subscription_id:
        logger.warning('PayPal webhook missing subscription id for %s', event_type)
        return JsonResponse({'ok': True, 'ignored': True})

    plan_id = resource.get('plan_id')
    plan = Plan.objects.filter(paypal_plan_id=plan_id).first() if plan_id else None

    status = map_paypal_status(resource.get('status'))
    started_at = parse_paypal_datetime(resource.get('start_time'))
    next_billing = None
    billing_info = resource.get('billing_info') or {}
    next_billing = parse_paypal_datetime(billing_info.get('next_billing_time'))

    subscription = ClinicSubscription.objects.filter(paypal_subscription_id=subscription_id).first()
    if not subscription:
        clinic = None
        custom_id = resource.get('custom_id') or ''
        if custom_id.startswith('clinic-'):
            try:
                clinic_id = int(custom_id.split('-', 1)[1])
            except (IndexError, ValueError):
                clinic_id = None
            if clinic_id:
                clinic = Clinic.objects.filter(id=clinic_id).first()

        if not clinic or not plan:
            logger.warning('PayPal webhook could not map subscription %s', subscription_id)
            return JsonResponse({'ok': True, 'ignored': True})

        subscription = ClinicSubscription.objects.create(
            clinic=clinic,
            plan=plan,
            paypal_subscription_id=subscription_id,
            status=status,
            started_at=started_at,
            current_period_end=next_billing,
            last_event_type=event_type,
        )
        return JsonResponse({'ok': True, 'status': subscription.status})

    if plan:
        subscription.plan = plan
    subscription.status = status
    subscription.started_at = started_at or subscription.started_at
    subscription.current_period_end = next_billing
    subscription.last_event_type = event_type
    subscription.save(update_fields=[
        'plan',
        'status',
        'started_at',
        'current_period_end',
        'last_event_type',
    ])

    return JsonResponse({'ok': True, 'status': subscription.status})
