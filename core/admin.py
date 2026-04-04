from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import QuerySet
from simple_history.admin import SimpleHistoryAdmin
from admin_interface.models import Theme
from .models import (
    Appointment,
    AppointmentType,
    AdminBranding,
    Clinic,
    ClinicMessagingPermission,
    ClinicSubscription,
    HelpRequest,
    Message,
    MessageThread,
    MessageThreadReadState,
    Patient,
    Plan,
    SecurityAccessRule,
    SecurityEvent,
    Staff,
    TwoFactorRecoveryCode,
    WaitlistEntry,
)

User = get_user_model()

admin.site.site_header = 'ClinicOps Admin'
admin.site.site_title = 'ClinicOps Admin'
admin.site.index_title = 'ClinicOps Administration'


def _admin_has_permission(request):
    if not request.user.is_active or not request.user.is_staff:
        return False
    return request.user.is_superuser


admin.site.has_permission = _admin_has_permission

_original_each_context = admin.site.each_context


def _dynamic_each_context(request):
    context = _original_each_context(request)
    branding = AdminBranding.get_solo()
    context['site_header'] = branding.site_header
    context['site_title'] = branding.site_title
    context['index_title'] = branding.index_title
    return context


admin.site.each_context = _dynamic_each_context


class ClinicScopedAdmin(admin.ModelAdmin):
    def _get_user_clinic(self, request):
        try:
            return request.user.staff.clinic
        except Staff.DoesNotExist:
            return None

    def get_queryset(self, request) -> QuerySet:
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        clinic = self._get_user_clinic(request)
        if not clinic:
            return qs.none()
        if hasattr(self.model, 'clinic'):
            return qs.filter(clinic=clinic)
        if self.model is Clinic:
            return qs.filter(pk=clinic.pk)
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if request.user.is_superuser:
            return super().formfield_for_foreignkey(db_field, request, **kwargs)

        clinic = self._get_user_clinic(request)
        if clinic and db_field.name == 'clinic':
            kwargs['queryset'] = Clinic.objects.filter(pk=clinic.pk)
        if clinic and db_field.name == 'staff':
            kwargs['queryset'] = Staff.objects.filter(clinic=clinic)
        if clinic and db_field.name == 'patient':
            kwargs['queryset'] = Patient.objects.filter(clinic=clinic)
        if clinic and db_field.name == 'appointment_type':
            kwargs['queryset'] = AppointmentType.objects.filter(clinic=clinic)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and hasattr(obj, 'clinic'):
            clinic = self._get_user_clinic(request)
            if clinic and not obj.clinic_id:
                obj.clinic = clinic
        super().save_model(request, obj, form, change)


@admin.register(Clinic)
class ClinicAdmin(ClinicScopedAdmin, SimpleHistoryAdmin):
    list_display = ('name', 'slug', 'owner_user', 'timezone', 'email', 'phone', 'brand_color', 'is_active', 'created_at')
    list_filter = ('is_active', 'timezone')
    search_fields = ('name', 'slug', 'email', 'phone')


@admin.register(Staff)
class StaffAdmin(ClinicScopedAdmin, SimpleHistoryAdmin):
    class StaffAdminForm(forms.ModelForm):
        email = forms.EmailField(required=False)
        first_name = forms.CharField(required=False)
        last_name = forms.CharField(required=False)
        password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True))
        role = forms.ChoiceField(
            choices=[
                ('', '---------'),
                ('Admin', 'Admin'),
                ('Doctor', 'Doctor'),
                ('Nurse', 'Nurse'),
                ('FrontDesk', 'Front Desk'),
            ],
            required=False,
        )

        class Meta:
            model = Staff
            fields = ('user', 'clinic', 'is_active', 'avatar')

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Allow creating staff without selecting an existing user.
            self.fields['user'].required = False
            if self.instance and self.instance.pk and self.instance.user_id:
                user = self.instance.user
                self.fields['email'].initial = user.email
                self.fields['first_name'].initial = user.first_name
                self.fields['last_name'].initial = user.last_name
                current_role = (
                    user.groups.filter(name__in=['Admin', 'Doctor', 'Nurse', 'FrontDesk'])
                    .values_list('name', flat=True)
                    .first()
                )
                if current_role:
                    self.fields['role'].initial = current_role

        def _get_validation_exclusions(self):
            exclude = super()._get_validation_exclusions()
            if not self.cleaned_data.get('user'):
                exclude.add('user')
            return exclude

        def clean(self):
            cleaned = super().clean()
            user = cleaned.get('user')
            email = cleaned.get('email')
            password = cleaned.get('password')
            role = cleaned.get('role')

            if not user:
                if not email:
                    self.add_error('email', 'Email is required to create a new staff user.')
                if not password:
                    self.add_error('password', 'Password is required to create a new staff user.')
                if not role:
                    self.add_error('role', 'Select a role for this staff user.')
                if email:
                    normalized = email.strip().lower()
                    if User.objects.filter(username__iexact=normalized).exists():
                        self.add_error('email', 'A user with this email already exists.')
                    cleaned['email'] = normalized
            else:
                if Staff.objects.filter(user=user).exclude(pk=self.instance.pk).exists():
                    self.add_error('user', 'This user already has a staff profile.')
            return cleaned

    list_display = ('user', 'clinic', 'is_active', 'avatar', 'created_at')
    list_filter = ('clinic', 'is_active')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    form = StaffAdminForm

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            if obj is None:
                # Hide user selection on add for clinic admins to avoid cross-clinic users.
                form.base_fields['user'].queryset = User.objects.none()
                form.base_fields['user'].widget = forms.HiddenInput()
            else:
                # Prevent reassignment of staff users by clinic admins.
                form.base_fields['user'].disabled = True
        return form

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'user' and not request.user.is_superuser:
            kwargs['queryset'] = User.objects.filter(staff__isnull=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        user = obj.user if obj.user_id else None
        if not user:
            email = form.cleaned_data.get('email')
            password = form.cleaned_data.get('password')
            first_name = form.cleaned_data.get('first_name') or ''
            last_name = form.cleaned_data.get('last_name') or ''
            if email and password:
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    password=password,
                    is_staff=True,
                    is_active=True,
                )
                obj.user = user
        if obj.user and not obj.user.is_staff:
            obj.user.is_staff = True
            obj.user.save(update_fields=['is_staff'])

        super().save_model(request, obj, form, change)

        role = form.cleaned_data.get('role')
        if role and obj.user:
            staff_groups = Group.objects.filter(name__in=['Admin', 'Doctor', 'Nurse', 'FrontDesk'])
            obj.user.groups.remove(*staff_groups)
            group = Group.objects.filter(name=role).first()
            if group:
                obj.user.groups.add(group)


@admin.register(Patient)
class PatientAdmin(ClinicScopedAdmin, SimpleHistoryAdmin):
    list_display = ('first_name', 'last_name', 'clinic', 'email', 'phone', 'user', 'avatar', 'created_at')
    list_filter = ('clinic',)
    search_fields = ('id',)


@admin.register(AppointmentType)
class AppointmentTypeAdmin(ClinicScopedAdmin, SimpleHistoryAdmin):
    list_display = ('name', 'clinic', 'duration_minutes', 'price_cents', 'is_active')
    list_filter = ('clinic', 'is_active')
    search_fields = ('name',)


@admin.register(Appointment)
class AppointmentAdmin(ClinicScopedAdmin, SimpleHistoryAdmin):
    list_display = ('clinic', 'appointment_type', 'staff', 'patient', 'start_at', 'end_at', 'status')
    list_filter = ('clinic', 'status', 'staff')
    search_fields = ('staff__user__username', 'patient__id')


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(ClinicScopedAdmin, SimpleHistoryAdmin):
    list_display = (
        'first_name',
        'last_name',
        'clinic',
        'appointment_type',
        'preferred_start_date',
        'preferred_end_date',
        'status',
        'created_at',
    )
    list_filter = ('clinic', 'status', 'appointment_type')
    search_fields = ('id',)


@admin.register(Plan)
class PlanAdmin(SimpleHistoryAdmin):
    list_display = (
        'name',
        'plan_mode',
        'interval',
        'price_display',
        'staff_limit_display',
        'service_limit_display',
        'monthly_appointment_limit_display',
        'includes_reminders',
        'includes_notifications',
        'includes_custom_branding',
        'is_active',
    )
    list_filter = ('is_free', 'interval', 'is_active', 'includes_reminders', 'includes_notifications')
    search_fields = ('name', 'paypal_plan_id')
    fieldsets = (
        (
            'Plan identity',
            {
                'fields': ('name', 'is_active', 'is_free'),
                'description': 'Use one Free plan and one Premium paid plan for the MVP.',
            },
        ),
        (
            'Billing',
            {
                'fields': ('interval', 'price_cents', 'currency', 'paypal_plan_id'),
                'description': 'Free plans keep PayPal plan ID empty. Paid plans need the matching PayPal plan ID.',
            },
        ),
        (
            'Usage limits',
            {
                'fields': ('staff_limit', 'service_limit', 'monthly_appointment_limit'),
                'description': 'Leave limits empty for unlimited usage on paid plans.',
            },
        ),
        (
            'Included features',
            {
                'fields': ('includes_reminders', 'includes_notifications', 'includes_custom_branding'),
            },
        ),
    )

    @admin.display(description='Mode')
    def plan_mode(self, obj):
        return 'Free' if obj.is_free else 'Paid'

    @admin.display(description='Price')
    def price_display(self, obj):
        return 'Free' if obj.is_free else f'{obj.currency} {obj.price_cents / 100:.2f}'

    @admin.display(description='Staff')
    def staff_limit_display(self, obj):
        return obj.staff_limit if obj.staff_limit is not None else 'Unlimited'

    @admin.display(description='Services')
    def service_limit_display(self, obj):
        return obj.service_limit if obj.service_limit is not None else 'Unlimited'

    @admin.display(description='Appointments / month')
    def monthly_appointment_limit_display(self, obj):
        return (
            obj.monthly_appointment_limit
            if obj.monthly_appointment_limit is not None
            else 'Unlimited'
        )


@admin.register(ClinicSubscription)
class ClinicSubscriptionAdmin(ClinicScopedAdmin, SimpleHistoryAdmin):
    list_display = ('clinic', 'plan', 'status', 'paypal_subscription_id', 'created_at')
    list_filter = ('status', 'plan')
    search_fields = ('clinic__name', 'paypal_subscription_id')


@admin.register(ClinicMessagingPermission)
class ClinicMessagingPermissionAdmin(ClinicScopedAdmin, admin.ModelAdmin):
    list_display = ('clinic', 'role', 'access_level', 'updated_at')
    list_filter = ('clinic', 'role', 'access_level')
    search_fields = ('clinic__name',)


@admin.register(MessageThread)
class MessageThreadAdmin(ClinicScopedAdmin, admin.ModelAdmin):
    list_display = ('clinic', 'patient', 'subject', 'appointment', 'status', 'last_message_sender_type', 'last_message_at')
    list_filter = ('clinic', 'status', 'source', 'last_message_sender_type')
    search_fields = ('subject', 'patient__id')


@admin.register(Message)
class MessageAdmin(ClinicScopedAdmin, admin.ModelAdmin):
    list_display = ('thread', 'sender_type', 'sender_user', 'sender_label', 'created_at')
    list_filter = ('sender_type', 'created_at')
    search_fields = ('thread__subject', 'sender_label')

    def get_queryset(self, request) -> QuerySet:
        qs = admin.ModelAdmin.get_queryset(self, request).select_related('thread', 'sender_user')
        if request.user.is_superuser:
            return qs
        clinic = self._get_user_clinic(request)
        if not clinic:
            return qs.none()
        return qs.filter(thread__clinic=clinic)


@admin.register(MessageThreadReadState)
class MessageThreadReadStateAdmin(ClinicScopedAdmin, admin.ModelAdmin):
    list_display = ('thread', 'user', 'last_read_at')
    list_filter = ('last_read_at',)
    search_fields = ('user__username', 'user__email')

    def get_queryset(self, request) -> QuerySet:
        qs = admin.ModelAdmin.get_queryset(self, request).select_related('thread', 'user')
        if request.user.is_superuser:
            return qs
        clinic = self._get_user_clinic(request)
        if not clinic:
            return qs.none()
        return qs.filter(thread__clinic=clinic)


@admin.register(HelpRequest)
class HelpRequestAdmin(ClinicScopedAdmin, admin.ModelAdmin):
    list_display = (
        'subject',
        'request_type',
        'clinic',
        'submitted_by',
        'staff_role',
        'priority',
        'status',
        'created_at',
    )
    list_filter = ('request_type', 'priority', 'status', 'clinic', 'category')
    search_fields = ('subject', 'reporter_name', 'reporter_email', 'page_url')
    readonly_fields = (
        'clinic',
        'submitted_by',
        'request_type',
        'category',
        'priority',
        'subject',
        'details',
        'business_impact',
        'page_url',
        'user_agent',
        'reporter_name',
        'reporter_email',
        'staff_role',
        'created_at',
        'updated_at',
    )
    fieldsets = (
        (
            'Request',
            {
                'fields': (
                    'clinic',
                    'submitted_by',
                    'request_type',
                    'category',
                    'priority',
                    'status',
                    'subject',
                    'details',
                    'business_impact',
                ),
            },
        ),
        (
            'Reporter context',
            {
                'fields': (
                    'reporter_name',
                    'reporter_email',
                    'staff_role',
                    'page_url',
                    'user_agent',
                ),
            },
        ),
        (
            'Internal follow-up',
            {
                'fields': ('internal_notes', 'resolved_at', 'created_at', 'updated_at'),
            },
        ),
    )


@admin.register(AdminBranding)
class AdminBrandingAdmin(admin.ModelAdmin):
    list_display = ('site_header', 'site_title', 'index_title', 'updated_at')

    def has_add_permission(self, request):
        return not AdminBranding.objects.exists()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        theme = Theme.objects.filter(active=True).first() or Theme.objects.first()
        if theme:
            theme.title = obj.site_header
            theme.name = obj.site_title
            theme.save(update_fields=['title', 'name'])


@admin.register(SecurityAccessRule)
class SecurityAccessRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'action', 'target_type', 'value', 'scope', 'is_active', 'updated_at')
    list_filter = ('action', 'target_type', 'scope', 'is_active')
    search_fields = ('name', 'value', 'note')
    list_editable = ('is_active',)
    fieldsets = (
        ('Rule', {
            'fields': ('name', 'action', 'target_type', 'scope', 'value', 'is_active'),
            'description': 'Whitelist or block an IP/CIDR range or a two-letter country code. Country matches depend on a proxy header such as CF-IPCountry.',
        }),
        ('Notes', {
            'fields': ('note',),
        }),
    )


@admin.register(SecurityEvent)
class SecurityEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'user', 'identifier', 'clinic', 'ip_address', 'country_code', 'created_at')
    list_filter = ('event_type', 'clinic', 'country_code')
    search_fields = ('user__username', 'user__email', 'identifier', 'ip_address', 'user_agent', 'country_code')
    readonly_fields = ('clinic', 'user', 'event_type', 'identifier', 'ip_address', 'country_code', 'user_agent', 'path', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TwoFactorRecoveryCode)
class TwoFactorRecoveryCodeAdmin(admin.ModelAdmin):
    list_display = ('user', 'code_suffix', 'consumed_at', 'created_at')
    list_filter = ('consumed_at',)
    search_fields = ('user__username', 'user__email', 'code_suffix')
    readonly_fields = ('user', 'code_hash', 'code_suffix', 'consumed_at', 'created_at')
