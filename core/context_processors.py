from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from .messaging import (
    build_thread_preview_rows,
    message_threads_for_patient,
    message_threads_for_staff,
    thread_meta_for_patient,
    thread_meta_for_staff,
    thread_title_for_patient,
    thread_title_for_staff,
    user_can_view_messages,
)
from .models import Notification, Patient, Staff
from .plan_limits import (
    clinic_can_use_messaging,
    clinic_can_use_notifications,
    clinic_can_use_waitlist,
    clinic_usage_summary,
)

ALLOWED_GROUPS = {'Admin', 'Doctor', 'Nurse', 'FrontDesk'}
SEARCHABLE_GROUPS = {'Admin', 'Doctor', 'FrontDesk'}


def user_roles(request):
    user = request.user
    brand_context = {
        'public_brand_name': getattr(settings, 'PUBLIC_BRAND_NAME', 'ClinicOps'),
        'public_brand_color': getattr(settings, 'PUBLIC_BRAND_COLOR', '#00488a'),
        'public_logo_url': getattr(settings, 'PUBLIC_LOGO_URL', ''),
        'google_oauth_enabled': getattr(settings, 'GOOGLE_OAUTH_ENABLED', False),
    }
    if not user.is_authenticated:
        return brand_context

    is_admin = user.is_superuser or user.groups.filter(name='Admin').exists()
    is_staff_user = user.is_superuser or user.groups.filter(name__in=ALLOWED_GROUPS).exists()
    can_staff_search = user.is_superuser or user.groups.filter(name__in=SEARCHABLE_GROUPS).exists()
    can_manage_waitlist = user.is_superuser or user.groups.filter(name__in={'Admin', 'FrontDesk'}).exists()

    clinic = None
    avatar_url = None
    patient_multi = False
    patient_clinic_options = []
    patient_selected_id = None
    unread_notifications = 0
    notification_preview = []
    notifications_enabled = True
    unread_messages = 0
    message_preview = []
    messages_visible = False
    messages_enabled = False
    waitlist_visible = False
    waitlist_enabled = False
    plan_usage = None

    try:
        try:
            staff = user.staff
            clinic = staff.clinic
            if staff.avatar:
                avatar_url = staff.avatar.url
            plan_usage = clinic_usage_summary(clinic)
            notifications_enabled = clinic_can_use_notifications(clinic, usage=plan_usage)
            messages_visible = user_can_view_messages(user, clinic)
            messages_enabled = messages_visible and clinic_can_use_messaging(clinic, usage=plan_usage)
            waitlist_visible = can_manage_waitlist and is_staff_user
            waitlist_enabled = waitlist_visible and clinic_can_use_waitlist(clinic, usage=plan_usage)
            if notifications_enabled:
                latest_notifications = list(
                    Notification.objects.filter(recipient=user)
                    .select_related('clinic')
                    .order_by('-created_at')[:5]
                )
                unread_notifications = Notification.objects.filter(recipient=user, is_read=False).count()
                default_tz = timezone.get_current_timezone()
                notification_preview = [
                    {
                        'id': item.id,
                        'title': item.title,
                        'body': item.body,
                        'link': item.link,
                        'level': item.level,
                        'is_read': item.is_read,
                        'created_at_label': timezone.localtime(
                            item.created_at,
                            ZoneInfo(item.clinic.timezone or 'UTC') if item.clinic else default_tz,
                        ).strftime('%b %d, %I:%M %p'),
                    }
                    for item in latest_notifications
                ]
            if messages_enabled:
                latest_threads = list(message_threads_for_staff(clinic)[:60])
                unread_messages, message_preview = build_thread_preview_rows(
                    user=user,
                    threads=latest_threads,
                    limit=5,
                    title_fn=thread_title_for_staff,
                    meta_fn=thread_meta_for_staff,
                )
        except Staff.DoesNotExist:
            patient_profiles = (
                Patient.objects.filter(user=user)
                .select_related('clinic')
                .order_by('clinic__name')
            )
            patient = None
            if patient_profiles.exists():
                patient_multi = patient_profiles.count() > 1
                selected_id = request.session.get('patient_clinic_id')
                patient_selected_id = selected_id
                if selected_id:
                    patient = patient_profiles.filter(clinic_id=selected_id).first()
                if not patient and patient_profiles.count() == 1:
                    patient = patient_profiles.first()
                patient_clinic_options = [
                    {'id': profile.clinic_id, 'name': profile.clinic.name}
                    for profile in patient_profiles
                ]
            if patient:
                clinic = patient.clinic
                if patient.avatar:
                    avatar_url = patient.avatar.url
                plan_usage = clinic_usage_summary(clinic)
                messages_visible = clinic_can_use_messaging(clinic, usage=plan_usage)
                messages_enabled = messages_visible
                if messages_enabled:
                    latest_threads = list(message_threads_for_patient(patient)[:60])
                    unread_messages, message_preview = build_thread_preview_rows(
                        user=user,
                        threads=latest_threads,
                        limit=5,
                        title_fn=thread_title_for_patient,
                        meta_fn=thread_meta_for_patient,
                    )
    except DatabaseError:
        clinic = None
        avatar_url = None
        patient_multi = False
        patient_clinic_options = []
        patient_selected_id = None
        unread_notifications = 0
        notification_preview = []
        notifications_enabled = True
        unread_messages = 0
        message_preview = []
        messages_visible = False
        messages_enabled = False
        waitlist_visible = False
        waitlist_enabled = False
        plan_usage = None

    return {
        **brand_context,
        'nav_is_admin': is_admin,
        'nav_is_staff': is_staff_user,
        'nav_clinic': clinic,
        'nav_avatar_url': avatar_url,
        'nav_patient_multi': patient_multi,
        'nav_patient_clinics': patient_clinic_options,
        'nav_patient_selected_id': patient_selected_id,
        'nav_notifications_enabled': notifications_enabled,
        'nav_notifications_unread_count': unread_notifications,
        'nav_notification_preview': notification_preview,
        'nav_messages_visible': messages_visible,
        'nav_messages_enabled': messages_enabled,
        'nav_messages_unread_count': unread_messages,
        'nav_message_preview': message_preview,
        'nav_plan_usage': plan_usage,
        'nav_can_search': can_staff_search and is_staff_user,
        'nav_waitlist_visible': waitlist_visible,
        'nav_waitlist_enabled': waitlist_enabled,
        'nav_can_manage_waitlist': waitlist_enabled,
    }
