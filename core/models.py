import secured_fields
from django.utils.crypto import get_random_string
from django.utils.text import slugify
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords

User = get_user_model()


class Clinic(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    timezone = models.CharField(max_length=64, default='UTC')
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=32, blank=True, null=True)
    logo_url = models.URLField(blank=True, null=True)
    brand_color = models.CharField(max_length=7, default='#1d4ed8')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            base = slugify(self.name) or 'clinic'
            slug = base[:200]
            counter = 2
            while Clinic.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                suffix = f'-{counter}'
                slug = f'{base[:200 - len(suffix)]}{suffix}'
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class AdminBranding(models.Model):
    site_header = models.CharField(max_length=100, default='ClinicOps Admin')
    site_title = models.CharField(max_length=100, default='ClinicOps Admin')
    index_title = models.CharField(max_length=200, default='ClinicOps Administration')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Admin Branding'
        verbose_name_plural = 'Admin Branding'

    def __str__(self) -> str:
        return self.site_header

    @classmethod
    def get_solo(cls):
        branding, _ = cls.objects.get_or_create(pk=1)
        return branding


class Staff(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='staff_members')
    avatar = models.ImageField(upload_to='avatars/staff/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    def __str__(self) -> str:
        return f'{self.user.get_full_name() or self.user.username}'


class AppointmentType(models.Model):
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='appointment_types')
    name = models.CharField(max_length=100)
    duration_minutes = models.PositiveIntegerField(default=30)
    price_cents = models.PositiveIntegerField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['name']
        unique_together = ('clinic', 'name')

    def __str__(self) -> str:
        return f'{self.name} ({self.duration_minutes} min)'


class Patient(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='patient_profiles',
    )
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='patients')
    avatar = models.ImageField(upload_to='avatars/patients/', blank=True, null=True)
    first_name = secured_fields.EncryptedCharField(max_length=100)
    last_name = secured_fields.EncryptedCharField(max_length=100)
    email = secured_fields.EncryptedCharField(max_length=254, searchable=True)
    phone = secured_fields.EncryptedCharField(max_length=32, searchable=True)
    dob = secured_fields.EncryptedDateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    def __str__(self) -> str:
        return f'{self.first_name} {self.last_name}'


class Appointment(models.Model):
    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='appointments')
    appointment_type = models.ForeignKey(
        AppointmentType,
        on_delete=models.SET_NULL,
        related_name='appointments',
        blank=True,
        null=True,
    )
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name='appointments')
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='appointments')
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    notes = secured_fields.EncryptedTextField(blank=True, null=True)
    cancel_reason = secured_fields.EncryptedTextField(blank=True, null=True)
    confirmation_code = models.CharField(max_length=12, blank=True, null=True, unique=True)
    reminder_sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['start_at']
        indexes = [
            models.Index(fields=['clinic']),
            models.Index(fields=['staff']),
            models.Index(fields=['start_at']),
        ]

    def __str__(self) -> str:
        return f'{self.patient} with {self.staff} @ {self.start_at}'

    @staticmethod
    def _generate_confirmation_code() -> str:
        return get_random_string(12).upper()

    def clean(self) -> None:
        if self.start_at and self.end_at and self.start_at >= self.end_at:
            raise ValidationError({'end_at': 'End time must be after start time.'})

        if self.staff_id and self.clinic_id and self.staff.clinic_id != self.clinic_id:
            raise ValidationError({'staff': 'Staff member must belong to the same clinic.'})

        if self.patient_id and self.clinic_id and self.patient.clinic_id != self.clinic_id:
            raise ValidationError({'patient': 'Patient must belong to the same clinic.'})

        if (
            self.appointment_type_id
            and self.clinic_id
            and self.appointment_type.clinic_id != self.clinic_id
        ):
            raise ValidationError({'appointment_type': 'Appointment type must belong to the same clinic.'})

        if self.staff_id and self.start_at and self.end_at:
            overlaps = Appointment.objects.filter(
                staff_id=self.staff_id,
                start_at__lt=self.end_at,
                end_at__gt=self.start_at,
            )
            if self.pk:
                overlaps = overlaps.exclude(pk=self.pk)
            if overlaps.exists():
                raise ValidationError('This appointment overlaps another appointment for the same staff member.')

    def save(self, *args, **kwargs) -> None:
        if not self.confirmation_code:
            for _ in range(5):
                candidate = self._generate_confirmation_code()
                if not Appointment.objects.filter(confirmation_code=candidate).exists():
                    self.confirmation_code = candidate
                    break
        self.full_clean()
        super().save(*args, **kwargs)


class Plan(models.Model):
    class Interval(models.TextChoices):
        MONTH = 'month', 'Monthly'
        YEAR = 'year', 'Yearly'

    name = models.CharField(max_length=100)
    paypal_plan_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    interval = models.CharField(max_length=10, choices=Interval.choices, default=Interval.MONTH)
    price_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=10, default='USD')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['price_cents', 'name']

    def __str__(self) -> str:
        return f'{self.name} ({self.currency} {self.price_cents / 100:.2f} / {self.interval})'

    @property
    def price_dollars(self) -> float:
        return self.price_cents / 100


class ClinicSubscription(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        ACTIVE = 'active', 'Active'
        CANCELLED = 'cancelled', 'Cancelled'
        SUSPENDED = 'suspended', 'Suspended'
        EXPIRED = 'expired', 'Expired'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='subscriptions')
    paypal_subscription_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(blank=True, null=True)
    current_period_end = models.DateTimeField(blank=True, null=True)
    cancel_at_period_end = models.BooleanField(default=False)
    last_event_type = models.CharField(max_length=64, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.clinic.name} - {self.plan.name} ({self.status})'


class PayPalWebhookEvent(models.Model):
    class ProcessingStatus(models.TextChoices):
        RECEIVED = 'received', 'Received'
        PROCESSED = 'processed', 'Processed'
        IGNORED = 'ignored', 'Ignored'
        FAILED = 'failed', 'Failed'

    event_id = models.CharField(max_length=128, unique=True)
    event_type = models.CharField(max_length=128, blank=True)
    resource_type = models.CharField(max_length=64, blank=True)
    resource_id = models.CharField(max_length=128, blank=True)
    summary = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.RECEIVED,
    )
    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    clinic_subscription = models.ForeignKey(
        ClinicSubscription,
        on_delete=models.SET_NULL,
        related_name='webhook_events',
        blank=True,
        null=True,
    )
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self) -> str:
        return f'{self.event_type or "PayPal event"} ({self.event_id})'
