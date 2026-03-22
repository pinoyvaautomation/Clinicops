from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .models import Notification, Patient, Staff

ALLOWED_GROUPS = {'Admin', 'Doctor', 'Nurse', 'FrontDesk'}


def user_roles(request):
    user = request.user
    brand_context = {
        'public_brand_name': getattr(settings, 'PUBLIC_BRAND_NAME', 'ClinicOps'),
        'public_brand_color': getattr(settings, 'PUBLIC_BRAND_COLOR', '#0f5132'),
        'public_logo_url': getattr(settings, 'PUBLIC_LOGO_URL', ''),
    }
    if not user.is_authenticated:
        return brand_context

    is_admin = user.is_superuser or user.groups.filter(name='Admin').exists()
    is_staff_user = user.is_superuser or user.groups.filter(name__in=ALLOWED_GROUPS).exists()

    clinic = None
    avatar_url = None
    patient_multi = False
    patient_clinic_options = []
    patient_selected_id = None
    unread_notifications = 0
    notification_preview = []

    try:
        staff = user.staff
        clinic = staff.clinic
        if staff.avatar:
            avatar_url = staff.avatar.url
        latest_notifications = list(
            Notification.objects.filter(recipient=user)
            .select_related('clinic')
            .order_by('-created_at')[:5]
        )
        unread_notifications = Notification.objects.filter(recipient=user, is_read=False).count()
        default_tz = timezone.get_current_timezone()
        notification_preview = [
            {
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

    return {
        **brand_context,
        'nav_is_admin': is_admin,
        'nav_is_staff': is_staff_user,
        'nav_clinic': clinic,
        'nav_avatar_url': avatar_url,
        'nav_patient_multi': patient_multi,
        'nav_patient_clinics': patient_clinic_options,
        'nav_patient_selected_id': patient_selected_id,
        'nav_notifications_unread_count': unread_notifications,
        'nav_notification_preview': notification_preview,
    }
