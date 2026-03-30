from datetime import timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.conf import settings
from django.utils import timezone

from .image_uploads import prepare_avatar_upload
from .models import Appointment, AppointmentType, Patient, Staff


class BookingForm(forms.Form):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField()
    phone = forms.CharField(max_length=32)
    dob = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    appointment_type_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    slot = forms.ChoiceField(choices=[])
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, slot_choices=None, appointment_type_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        if slot_choices is not None:
            self.fields['slot'].choices = slot_choices
        if appointment_type_id is not None:
            self.fields['appointment_type_id'].initial = appointment_type_id
        self.fields['first_name'].widget.attrs.update(
            {
                'placeholder': 'Jamie',
                'autocomplete': 'given-name',
            }
        )
        self.fields['last_name'].widget.attrs.update(
            {
                'placeholder': 'Patient',
                'autocomplete': 'family-name',
            }
        )
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'patient@email.com',
                'autocomplete': 'email',
            }
        )
        self.fields['phone'].widget.attrs.update(
            {
                'placeholder': '+63 912 345 6789',
                'autocomplete': 'tel',
            }
        )
        self.fields['notes'].widget.attrs.update(
            {
                'placeholder': 'Optional booking notes',
            }
        )


class ClinicAuthenticationForm(AuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get("username")
        if username:
            username = username.strip()
            if "@" in username:
                username = username.lower()
            UserModel = get_user_model()
            try:
                user = UserModel._default_manager.get_by_natural_key(username)
            except UserModel.DoesNotExist:
                user = None
            if user and not user.is_active:
                raise ValidationError(
                    'Please verify your email before signing in.',
                    code='inactive',
                )
        return super().clean()

    def confirm_login_allowed(self, user):
        if not user.is_active:
            raise ValidationError(
                'Please verify your email before signing in.',
                code='inactive',
            )


class AppointmentLookupForm(forms.Form):
    email = forms.EmailField()
    confirmation_code = forms.CharField(max_length=12)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'patient@email.com',
                'autocomplete': 'email',
            }
        )
        self.fields['confirmation_code'].widget.attrs.update(
            {
                'placeholder': 'ABC123',
                'autocomplete': 'off',
                'style': 'text-transform: uppercase;',
            }
        )


class ClinicSignupForm(forms.Form):
    clinic_name = forms.CharField(max_length=255)
    timezone = forms.CharField(max_length=64, required=False, initial='UTC')
    admin_first_name = forms.CharField(max_length=100)
    admin_last_name = forms.CharField(max_length=100)
    admin_email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput())
    confirm_password = forms.CharField(widget=forms.PasswordInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['clinic_name'].widget.attrs.update(
            {
                'placeholder': 'Clinica JMC',
                'autocomplete': 'organization',
            }
        )
        self.fields['timezone'].widget.attrs.update(
            {
                'placeholder': 'UTC',
                'autocomplete': 'off',
            }
        )
        self.fields['admin_first_name'].widget.attrs.update(
            {
                'placeholder': 'Maria',
                'autocomplete': 'given-name',
            }
        )
        self.fields['admin_last_name'].widget.attrs.update(
            {
                'placeholder': 'Santos',
                'autocomplete': 'family-name',
            }
        )
        self.fields['admin_email'].widget.attrs.update(
            {
                'placeholder': 'owner@clinic.com',
                'autocomplete': 'email',
            }
        )
        self.fields['password'].widget.attrs.update(
            {
                'placeholder': 'Create a secure password',
                'autocomplete': 'new-password',
            }
        )
        self.fields['confirm_password'].widget.attrs.update(
            {
                'placeholder': 'Repeat your password',
                'autocomplete': 'new-password',
            }
        )

    def clean_admin_email(self):
        email = self.cleaned_data['admin_email'].strip().lower()
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        confirm = cleaned.get('confirm_password')
        if password and confirm and password != confirm:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned


class PatientSignupForm(forms.Form):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField()
    phone = forms.CharField(max_length=32)
    dob = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    password = forms.CharField(widget=forms.PasswordInput())
    confirm_password = forms.CharField(widget=forms.PasswordInput())

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].widget.attrs.update(
            {
                'placeholder': 'Jamie',
                'autocomplete': 'given-name',
            }
        )
        self.fields['last_name'].widget.attrs.update(
            {
                'placeholder': 'Patient',
                'autocomplete': 'family-name',
            }
        )
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'patient@email.com',
                'autocomplete': 'email',
            }
        )
        self.fields['phone'].widget.attrs.update(
            {
                'placeholder': '+63 912 345 6789',
                'autocomplete': 'tel',
            }
        )
        self.fields['password'].widget.attrs.update(
            {
                'placeholder': 'Create a password',
                'autocomplete': 'new-password',
            }
        )
        self.fields['confirm_password'].widget.attrs.update(
            {
                'placeholder': 'Repeat your password',
                'autocomplete': 'new-password',
            }
        )

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get('password')
        confirm = cleaned.get('confirm_password')
        if password and confirm and password != confirm:
            self.add_error('confirm_password', 'Passwords do not match.')
        return cleaned


class ResendVerificationForm(forms.Form):
    email = forms.EmailField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'owner@clinic.com',
                'autocomplete': 'email',
            }
        )

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class AvatarUploadForm(forms.Form):
    avatar = forms.ImageField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['avatar'].widget.attrs.update(
            {
                'accept': 'image/jpeg,image/png,image/webp',
            }
        )
        self.fields['avatar'].help_text = (
            'Upload JPG, PNG, or WebP up to 1 MB. Avatars are resized automatically.'
        )

    def clean_avatar(self):
        """Normalize avatar uploads so the settings view can save them directly."""
        avatar = self.cleaned_data.get('avatar')
        if not avatar:
            return avatar
        return prepare_avatar_upload(avatar)


class AppointmentTypeForm(forms.Form):
    name = forms.CharField(max_length=100)
    duration_minutes = forms.IntegerField(min_value=5)
    price_cents = forms.IntegerField(required=False, min_value=0)
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, clinic=None, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.clinic = clinic
        self.instance = instance
        self.fields['name'].widget.attrs.update(
            {
                'placeholder': 'General checkup',
                'autocomplete': 'off',
            }
        )
        self.fields['duration_minutes'].widget.attrs.update(
            {
                'placeholder': '30',
                'inputmode': 'numeric',
            }
        )
        self.fields['price_cents'].widget.attrs.update(
            {
                'placeholder': '500',
                'inputmode': 'numeric',
            }
        )
        if instance is not None and not self.is_bound:
            self.fields['name'].initial = instance.name
            self.fields['duration_minutes'].initial = instance.duration_minutes
            self.fields['price_cents'].initial = instance.price_cents
            self.fields['is_active'].initial = instance.is_active

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if self.clinic:
            qs = AppointmentType.objects.filter(clinic=self.clinic, name__iexact=name)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError('An appointment type with this name already exists.')
        return name


STAFF_ROLE_CHOICES = [
    ('Admin', 'Admin'),
    ('Doctor', 'Doctor'),
    ('Nurse', 'Nurse'),
    ('FrontDesk', 'Front Desk'),
]


class StaffMemberCreateForm(forms.Form):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    password = forms.CharField(widget=forms.PasswordInput())
    role = forms.ChoiceField(choices=STAFF_ROLE_CHOICES)
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'staff@clinic.com',
                'autocomplete': 'email',
            }
        )
        self.fields['first_name'].widget.attrs.update(
            {
                'placeholder': 'Maria',
                'autocomplete': 'given-name',
            }
        )
        self.fields['last_name'].widget.attrs.update(
            {
                'placeholder': 'Santos',
                'autocomplete': 'family-name',
            }
        )
        self.fields['password'].widget.attrs.update(
            {
                'placeholder': 'Create a temporary password',
                'autocomplete': 'new-password',
            }
        )

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class StaffMemberUpdateForm(forms.Form):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    password = forms.CharField(widget=forms.PasswordInput(render_value=True), required=False)
    role = forms.ChoiceField(choices=STAFF_ROLE_CHOICES)
    is_active = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'staff@clinic.com',
                'autocomplete': 'email',
            }
        )
        self.fields['first_name'].widget.attrs.update(
            {
                'placeholder': 'Maria',
                'autocomplete': 'given-name',
            }
        )
        self.fields['last_name'].widget.attrs.update(
            {
                'placeholder': 'Santos',
                'autocomplete': 'family-name',
            }
        )
        self.fields['password'].widget.attrs.update(
            {
                'placeholder': 'Leave blank to keep the current password',
                'autocomplete': 'new-password',
            }
        )

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class AppointmentUpdateForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ['status', 'notes']
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class AppointmentFrontDeskUpdateForm(forms.ModelForm):
    confirm_short_notice = forms.BooleanField(
        required=False,
        label='I confirm cancelling within 24 hours',
    )
    cancel_reason_choice = forms.ChoiceField(
        required=False,
        choices=[
            ('', 'Select a reason'),
            ('patient_no_show', 'Patient no-show'),
            ('doctor_unavailable', 'Doctor unavailable'),
            ('requested_reschedule', 'Patient requested reschedule'),
            ('walk_in_overflow', 'Walk-in overflow'),
            ('clinic_emergency', 'Clinic emergency'),
            ('other', 'Other (specify)'),
        ],
    )
    cancel_reason_other = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    class Meta:
        model = Appointment
        fields = ['staff', 'status', 'start_at']
        widgets = {
            'start_at': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, staff_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if staff_qs is not None:
            self.fields['staff'].queryset = staff_qs
        # Front desk can only keep scheduled or cancel appointments.
        self.fields['status'].choices = [
            (Appointment.Status.SCHEDULED, 'Scheduled'),
            (Appointment.Status.CANCELLED, 'Cancelled'),
        ]
        if self.instance and getattr(self.instance, 'cancel_reason', None):
            reason = self.instance.cancel_reason or ''
            if reason.startswith('other:'):
                self.fields['cancel_reason_choice'].initial = 'other'
                self.fields['cancel_reason_other'].initial = reason.replace('other:', '', 1).strip()
            elif reason in dict(self.fields['cancel_reason_choice'].choices):
                self.fields['cancel_reason_choice'].initial = reason
            else:
                self.fields['cancel_reason_choice'].initial = 'other'
                self.fields['cancel_reason_other'].initial = reason

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')
        start_at = cleaned.get('start_at')
        reason_choice = cleaned.get('cancel_reason_choice')
        reason_other = cleaned.get('cancel_reason_other')

        clinic = getattr(self.instance, 'clinic', None)
        tz = ZoneInfo(clinic.timezone or 'UTC') if clinic else timezone.get_current_timezone()

        if start_at and timezone.is_naive(start_at):
            start_at = timezone.make_aware(start_at, tz)
            cleaned['start_at'] = start_at

        duration_minutes = (
            self.instance.appointment_type.duration_minutes
            if self.instance and self.instance.appointment_type
            else getattr(settings, 'APPOINTMENT_SLOT_MINUTES', 30)
        )
        if start_at and self.instance:
            original_date = timezone.localtime(self.instance.start_at, tz).date()
            new_date = timezone.localtime(start_at, tz).date()
            if new_date != original_date:
                self.add_error('start_at', 'Front desk can only reschedule within the same day.')

            day_start = getattr(settings, 'APPOINTMENT_DAY_START', 9)
            day_end = getattr(settings, 'APPOINTMENT_DAY_END', 17)
            local_start = timezone.localtime(start_at, tz)
            local_end = local_start + timedelta(minutes=duration_minutes)
            if local_end.date() != local_start.date():
                self.add_error('start_at', 'Rescheduled time must stay within the same day.')
            if local_start.hour < day_start or (local_end.hour > day_end or (local_end.hour == day_end and local_end.minute > 0)):
                self.add_error('start_at', 'Rescheduled time must be within clinic hours.')

        # Prevent overlaps with other appointments for the selected staff.
        staff = cleaned.get('staff') or getattr(self.instance, 'staff', None)
        if staff and start_at:
            end_at = start_at + timedelta(minutes=duration_minutes)
            # Ensure model validation sees a valid end_at.
            self.instance.start_at = start_at
            self.instance.end_at = end_at
            overlaps = Appointment.objects.filter(
                staff=staff,
                start_at__lt=end_at,
                end_at__gt=start_at,
            )
            if self.instance and self.instance.pk:
                overlaps = overlaps.exclude(pk=self.instance.pk)
            if overlaps.exists():
                self.add_error('start_at', 'This time overlaps another appointment for this staff.')

        if status == Appointment.Status.CANCELLED:
            if not reason_choice:
                self.add_error('cancel_reason_choice', 'Select a cancellation reason.')
            if reason_choice == 'other':
                if not reason_other:
                    self.add_error('cancel_reason_other', 'Provide a cancellation reason.')
                else:
                    cleaned['cancel_reason'] = f'other: {reason_other.strip()}'
            elif reason_choice:
                cleaned['cancel_reason'] = reason_choice

            compare_time = start_at or self.instance.start_at
            if compare_time:
                delta = compare_time - timezone.now()
                if delta <= timedelta(hours=24):
                    if not cleaned.get('confirm_short_notice'):
                        self.add_error('confirm_short_notice', 'Confirm cancellation within 24 hours.')
        else:
            cleaned['cancel_reason'] = None

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.cancel_reason = self.cleaned_data.get('cancel_reason')
        if commit:
            instance.save()
        return instance


class PatientUpdateForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = ['first_name', 'last_name', 'email', 'phone', 'dob']
        widgets = {
            'dob': forms.DateInput(attrs={'type': 'date'}),
        }


class WalkInAppointmentForm(forms.Form):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField()
    phone = forms.CharField(max_length=32)
    dob = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    staff = forms.ModelChoiceField(queryset=Staff.objects.none())
    appointment_type = forms.ModelChoiceField(
        queryset=AppointmentType.objects.none(),
        required=False,
    )
    start_at = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, staff_qs=None, appointment_type_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if staff_qs is not None:
            self.fields['staff'].queryset = staff_qs
        if appointment_type_qs is not None:
            self.fields['appointment_type'].queryset = appointment_type_qs
        self.fields['first_name'].widget.attrs.update(
            {
                'placeholder': 'Patient first name',
                'autocomplete': 'given-name',
            }
        )
        self.fields['last_name'].widget.attrs.update(
            {
                'placeholder': 'Patient last name',
                'autocomplete': 'family-name',
            }
        )
        self.fields['email'].widget.attrs.update(
            {
                'placeholder': 'patient@email.com',
                'autocomplete': 'email',
            }
        )
        self.fields['phone'].widget.attrs.update(
            {
                'placeholder': '+63 912 345 6789',
                'autocomplete': 'tel',
            }
        )
        self.fields['start_at'].widget.attrs.update(
            {
                'step': 300,
            }
        )
        self.fields['notes'].widget.attrs.update(
            {
                'placeholder': 'Optional intake or scheduling notes',
            }
        )
