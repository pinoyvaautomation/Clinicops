from .models import Patient, Staff

ALLOWED_GROUPS = {'Admin', 'Doctor', 'Nurse', 'FrontDesk'}


def user_roles(request):
    user = request.user
    if not user.is_authenticated:
        return {}

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
        patient = getattr(user, 'patient_profile', None)
        if patient:
            clinic = patient.clinic
            if patient.avatar:
                avatar_url = patient.avatar.url

    return {
        'nav_is_admin': is_admin,
        'nav_is_staff': is_staff_user,
        'nav_clinic': clinic,
        'nav_avatar_url': avatar_url,
    }
