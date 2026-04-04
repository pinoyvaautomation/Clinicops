import secured_fields
import ipaddress
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from simple_history.models import HistoricalRecords

from .timezones import timezone_display_label

User = get_user_model()


class Clinic(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    timezone = models.CharField(max_length=64, default='UTC')
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=32, blank=True, null=True)
    owner_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='owned_clinics',
        blank=True,
        null=True,
    )
    logo_url = models.URLField(blank=True, null=True)
    brand_color = models.CharField(max_length=7, default='#1d4ed8')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    def __str__(self) -> str:
        return self.name

    @property
    def timezone_label(self) -> str:
        return timezone_display_label(self.timezone)

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
    intake_reason = secured_fields.EncryptedTextField(blank=True, null=True)
    intake_details = secured_fields.EncryptedTextField(blank=True, null=True)
    consent_to_treatment = models.BooleanField(default=False)
    consent_to_privacy = models.BooleanField(default=False)
    consent_signature_name = secured_fields.EncryptedCharField(max_length=150, blank=True, null=True)
    consent_signed_at = models.DateTimeField(blank=True, null=True)
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


class WaitlistEntry(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        CONTACTED = 'contacted', 'Contacted'
        BOOKED = 'booked', 'Booked'
        CLOSED = 'closed', 'Closed'

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name='waitlist_entries')
    appointment_type = models.ForeignKey(
        AppointmentType,
        on_delete=models.SET_NULL,
        related_name='waitlist_entries',
        blank=True,
        null=True,
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.SET_NULL,
        related_name='waitlist_entries',
        blank=True,
        null=True,
    )
    first_name = secured_fields.EncryptedCharField(max_length=100)
    last_name = secured_fields.EncryptedCharField(max_length=100)
    email = secured_fields.EncryptedCharField(max_length=254, searchable=True)
    phone = secured_fields.EncryptedCharField(max_length=32, searchable=True)
    preferred_start_date = models.DateField(blank=True, null=True)
    preferred_end_date = models.DateField(blank=True, null=True)
    notes = secured_fields.EncryptedTextField(blank=True, null=True)
    consent_to_contact = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ['status', 'preferred_start_date', 'created_at']
        indexes = [
            models.Index(fields=['clinic', 'status', 'created_at']),
        ]

    def __str__(self) -> str:
        service = self.appointment_type.name if self.appointment_type else 'General booking'
        return f'{self.first_name} {self.last_name} - {service} ({self.status})'


class Plan(models.Model):
    class Interval(models.TextChoices):
        MONTH = 'month', 'Monthly'
        YEAR = 'year', 'Yearly'

    name = models.CharField(max_length=100)
    is_free = models.BooleanField(default=False)
    paypal_plan_id = models.CharField(max_length=64, unique=True, blank=True, null=True)
    interval = models.CharField(max_length=10, choices=Interval.choices, default=Interval.MONTH)
    price_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=10, default='USD')
    # Plan notes: null limits mean unlimited, which is how Premium is modeled.
    staff_limit = models.PositiveIntegerField(blank=True, null=True)
    service_limit = models.PositiveIntegerField(blank=True, null=True)
    monthly_appointment_limit = models.PositiveIntegerField(blank=True, null=True)
    includes_reminders = models.BooleanField(default=True)
    includes_notifications = models.BooleanField(default=True)
    includes_custom_branding = models.BooleanField(default=True)
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


class Notification(models.Model):
    class Level(models.TextChoices):
        INFO = 'info', 'Info'
        SUCCESS = 'success', 'Success'
        WARNING = 'warning', 'Warning'
        ERROR = 'error', 'Error'

    class EventType(models.TextChoices):
        GENERIC = 'generic', 'Generic'
        ONLINE_BOOKING_CREATED = 'online_booking_created', 'Online Booking Created'
        APPOINTMENT_CREATED = 'appointment_created', 'Appointment Created'
        APPOINTMENT_UPDATED = 'appointment_updated', 'Appointment Updated'
        STAFF_ADDED = 'staff_added', 'Staff Added'
        STAFF_UPDATED = 'staff_updated', 'Staff Updated'
        SERVICE_ADDED = 'service_added', 'Service Added'
        SERVICE_UPDATED = 'service_updated', 'Service Updated'
        PATIENT_SIGNED_UP = 'patient_signed_up', 'Patient Signed Up'
        SUBSCRIPTION_ACTIVATED = 'subscription_activated', 'Subscription Activated'
        SUBSCRIPTION_UPDATED = 'subscription_updated', 'Subscription Updated'

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name='notifications',
        blank=True,
        null=True,
    )
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='triggered_notifications',
        blank=True,
        null=True,
    )
    event_type = models.CharField(
        max_length=64,
        choices=EventType.choices,
        default=EventType.GENERIC,
    )
    level = models.CharField(
        max_length=16,
        choices=Level.choices,
        default=Level.INFO,
    )
    title = models.CharField(max_length=140)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(fields=['clinic', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.title} -> {self.recipient}'

    def mark_read(self, *, when=None):
        if self.is_read:
            return
        self.is_read = True
        self.read_at = when or timezone.now()
        self.save(update_fields=['is_read', 'read_at'])


class ClinicMessagingPermission(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'Admin', 'Admin'
        DOCTOR = 'Doctor', 'Doctor'
        NURSE = 'Nurse', 'Nurse'
        FRONT_DESK = 'FrontDesk', 'Front Desk'

    class AccessLevel(models.TextChoices):
        NONE = 'none', 'No access'
        VIEW_ONLY = 'view', 'View only'
        REPLY = 'reply', 'View and reply'

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name='messaging_permissions',
    )
    role = models.CharField(max_length=32, choices=Role.choices)
    access_level = models.CharField(
        max_length=16,
        choices=AccessLevel.choices,
        default=AccessLevel.NONE,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['role']
        constraints = [
            models.UniqueConstraint(fields=['clinic', 'role'], name='uniq_clinic_messaging_role'),
        ]

    def __str__(self) -> str:
        return f'{self.clinic.name} · {self.get_role_display()} ({self.get_access_level_display()})'


class MessageThread(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        CLOSED = 'closed', 'Closed'

    class Source(models.TextChoices):
        APPOINTMENT = 'appointment', 'Appointment link'
        PORTAL = 'portal', 'Patient portal'

    class SenderType(models.TextChoices):
        PATIENT = 'patient', 'Patient'
        STAFF = 'staff', 'Staff'
        SYSTEM = 'system', 'System'

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name='message_threads',
    )
    patient = models.ForeignKey(
        Patient,
        on_delete=models.CASCADE,
        related_name='message_threads',
    )
    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.SET_NULL,
        related_name='message_threads',
        blank=True,
        null=True,
    )
    subject = models.CharField(max_length=140, blank=True)
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.PORTAL,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
    )
    last_message_sender_type = models.CharField(
        max_length=16,
        choices=SenderType.choices,
        default=SenderType.PATIENT,
    )
    last_message_excerpt = models.CharField(max_length=180, blank=True)
    last_message_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        indexes = [
            models.Index(fields=['clinic', 'status', '-last_message_at']),
            models.Index(fields=['patient', '-last_message_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['appointment'],
                condition=models.Q(appointment__isnull=False),
                name='uniq_message_thread_per_appointment',
            ),
        ]

    def __str__(self) -> str:
        label = self.subject or f'Messages for {self.patient}'
        return f'{self.clinic.name} · {label}'


class Message(models.Model):
    class SenderType(models.TextChoices):
        PATIENT = 'patient', 'Patient'
        STAFF = 'staff', 'Staff'
        SYSTEM = 'system', 'System'

    thread = models.ForeignKey(
        MessageThread,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='sent_thread_messages',
        blank=True,
        null=True,
    )
    sender_type = models.CharField(max_length=16, choices=SenderType.choices)
    sender_label = models.CharField(max_length=150, blank=True)
    body = secured_fields.EncryptedTextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['thread', 'created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.get_sender_type_display()} message in thread {self.thread_id}'


class MessageThreadReadState(models.Model):
    thread = models.ForeignKey(
        MessageThread,
        on_delete=models.CASCADE,
        related_name='read_states',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='message_thread_read_states',
    )
    last_read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['thread', 'user'], name='uniq_thread_read_state'),
        ]
        indexes = [
            models.Index(fields=['user', 'last_read_at']),
        ]

    def __str__(self) -> str:
        return f'{self.user} read thread {self.thread_id}'


class HelpRequest(models.Model):
    class RequestType(models.TextChoices):
        SUPPORT = 'support', 'Support request'
        FEATURE = 'feature', 'Feature request'

    class Status(models.TextChoices):
        NEW = 'new', 'New'
        IN_REVIEW = 'in_review', 'In review'
        PLANNED = 'planned', 'Planned'
        RESOLVED = 'resolved', 'Resolved'
        CLOSED = 'closed', 'Closed'

    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'
        URGENT = 'urgent', 'Urgent'

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name='help_requests',
    )
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='help_requests',
        blank=True,
        null=True,
    )
    request_type = models.CharField(max_length=16, choices=RequestType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    category = models.CharField(max_length=32, blank=True)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.MEDIUM)
    subject = models.CharField(max_length=140)
    details = secured_fields.EncryptedTextField()
    business_impact = secured_fields.EncryptedTextField(blank=True, null=True)
    page_url = models.CharField(max_length=255, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    reporter_name = models.CharField(max_length=150, blank=True)
    reporter_email = models.EmailField(blank=True)
    staff_role = models.CharField(max_length=32, blank=True)
    internal_notes = secured_fields.EncryptedTextField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['clinic', 'request_type', 'status', '-created_at']),
            models.Index(fields=['submitted_by', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.get_request_type_display()} - {self.subject}'


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


class SecurityEvent(models.Model):
    class EventType(models.TextChoices):
        LOGIN_SUCCESS = 'login_success', 'Login success'
        LOGIN_FAILED = 'login_failed', 'Login failed'
        LOGOUT = 'logout', 'Logout'
        PASSWORD_CHANGED = 'password_changed', 'Password changed'
        RATE_LIMITED = 'rate_limited', 'Rate limited'
        ACCESS_BLOCKED = 'access_blocked', 'Access blocked'
        TWO_FACTOR_ENABLED = 'two_factor_enabled', 'Two-factor enabled'
        TWO_FACTOR_DISABLED = 'two_factor_disabled', 'Two-factor disabled'
        TWO_FACTOR_CHALLENGE_PASSED = 'two_factor_challenge_passed', 'Two-factor challenge passed'
        TWO_FACTOR_CHALLENGE_FAILED = 'two_factor_challenge_failed', 'Two-factor challenge failed'
        TWO_FACTOR_RECOVERY_USED = 'two_factor_recovery_used', 'Two-factor recovery used'
        TWO_FACTOR_RESET = 'two_factor_reset', 'Two-factor reset'

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.SET_NULL,
        related_name='security_events',
        blank=True,
        null=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='security_events',
        blank=True,
        null=True,
    )
    event_type = models.CharField(max_length=64, choices=EventType.choices)
    identifier = models.CharField(max_length=254, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    country_code = models.CharField(max_length=8, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    path = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['clinic', '-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['event_type', '-created_at']),
            models.Index(fields=['country_code', '-created_at']),
        ]

    def __str__(self) -> str:
        target = self.user or self.identifier or 'unknown user'
        return f'{self.get_event_type_display()} ({target})'


class SecurityAccessRule(models.Model):
    class Action(models.TextChoices):
        ALLOW = 'allow', 'Whitelist'
        BLOCK = 'block', 'Block'

    class TargetType(models.TextChoices):
        IP = 'ip', 'IP or CIDR'
        COUNTRY = 'country', 'Country code'

    class Scope(models.TextChoices):
        AUTH = 'auth', 'Auth-sensitive routes'
        GLOBAL = 'global', 'Whole site'

    name = models.CharField(max_length=100)
    action = models.CharField(max_length=16, choices=Action.choices)
    target_type = models.CharField(max_length=16, choices=TargetType.choices)
    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        default=Scope.AUTH,
    )
    value = models.CharField(max_length=64)
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['action', 'target_type', 'value']
        indexes = [
            models.Index(fields=['is_active', 'action', 'target_type']),
            models.Index(fields=['scope', 'is_active']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['action', 'target_type', 'scope', 'value'],
                name='unique_security_access_rule',
            )
        ]

    def clean(self):
        value = (self.value or '').strip()
        if self.target_type == self.TargetType.IP:
            try:
                if '/' in value:
                    value = str(ipaddress.ip_network(value, strict=False))
                else:
                    value = str(ipaddress.ip_address(value))
            except ValueError as exc:
                raise ValidationError({'value': f'Enter a valid IP address or CIDR range. ({exc})'})
        else:
            value = value.upper()
            if len(value) != 2 or not value.isalpha():
                raise ValidationError({'value': 'Enter a two-letter country code such as PH or SG.'})
        self.value = value

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f'{self.get_action_display()} {self.get_target_type_display()}: {self.value}'


class TwoFactorRecoveryCode(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='two_factor_recovery_codes',
    )
    code_hash = models.CharField(max_length=64)
    code_suffix = models.CharField(max_length=6)
    consumed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['user_id', 'created_at', 'id']
        indexes = [
            models.Index(fields=['user', 'consumed_at']),
        ]

    def __str__(self) -> str:
        status = 'used' if self.consumed_at else 'active'
        return f'Recovery code ending {self.code_suffix} ({status})'
