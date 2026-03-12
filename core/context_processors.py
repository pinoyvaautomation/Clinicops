from django.conf import settings

from .models import Patient, Staff

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

    try:
        staff = user.staff
        clinic = staff.clinic
        if staff.avatar:
            avatar_url = staff.avatar.url
    except Staff.DoesNotExist:
        try:
            patient = user.patient_profile
        except Patient.DoesNotExist:
            patient = None
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
    }
