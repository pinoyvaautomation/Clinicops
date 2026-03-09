from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Appointment, AppointmentType, Clinic, Patient, Plan, Staff

User = get_user_model()


class Command(BaseCommand):
    help = 'Create sample clinic, staff, patient, and appointments.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='staff1', help='Username for sample staff user.')
        parser.add_argument('--password', default='password123', help='Password for sample staff user.')
        parser.add_argument('--clinic', default='Sample Clinic', help='Clinic name.')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        clinic_name = options['clinic']

        clinic, _ = Clinic.objects.get_or_create(
            name=clinic_name,
            defaults={
                'timezone': 'UTC',
                'email': 'info@sampleclinic.local',
                'phone': '555-0100',
                'brand_color': '#1d4ed8',
                'logo_url': 'https://placehold.co/120x48?text=Clinic',
            },
        )
        if not clinic.brand_color:
            clinic.brand_color = '#1d4ed8'
            clinic.save(update_fields=['brand_color'])

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': f'{username}@clinicops.local',
                'first_name': 'Sam',
                'last_name': 'Staff',
                'is_staff': True,
            },
        )
        if created:
            user.set_password(password)
            user.save(update_fields=['password'])
        elif not user.is_staff:
            user.is_staff = True
            user.save(update_fields=['is_staff'])

        staff, _ = Staff.objects.get_or_create(user=user, defaults={'clinic': clinic})
        if staff.clinic_id != clinic.id:
            staff.clinic = clinic
            staff.save(update_fields=['clinic'])

        admin_group = Group.objects.filter(name='Admin').first()
        if admin_group and not user.groups.filter(id=admin_group.id).exists():
            user.groups.add(admin_group)

        patient, _ = Patient.objects.get_or_create(
            clinic=clinic,
            email='patient1@clinicops.local',
            defaults={
                'first_name': 'Pat',
                'last_name': 'Client',
                'phone': '555-0123',
            },
        )

        consult_type, _ = AppointmentType.objects.get_or_create(
            clinic=clinic,
            name='Consultation',
            defaults={'duration_minutes': 30},
        )
        followup_type, _ = AppointmentType.objects.get_or_create(
            clinic=clinic,
            name='Follow-up',
            defaults={'duration_minutes': 45},
        )

        Plan.objects.get_or_create(
            name='Basic',
            paypal_plan_id='P-TEST-PLACEHOLDER',
            defaults={
                'interval': Plan.Interval.MONTH,
                'price_cents': 1500,
                'currency': 'USD',
                'is_active': True,
            },
        )

        now = timezone.now()
        appointment_1_start = now + timedelta(days=1, hours=2)
        appointment_1_end = appointment_1_start + timedelta(minutes=30)
        appointment_2_start = now + timedelta(days=2, hours=3)
        appointment_2_end = appointment_2_start + timedelta(minutes=45)

        Appointment.objects.get_or_create(
            clinic=clinic,
            appointment_type=consult_type,
            staff=staff,
            patient=patient,
            start_at=appointment_1_start,
            end_at=appointment_1_end,
            defaults={'status': Appointment.Status.SCHEDULED},
        )
        Appointment.objects.get_or_create(
            clinic=clinic,
            appointment_type=followup_type,
            staff=staff,
            patient=patient,
            start_at=appointment_2_start,
            end_at=appointment_2_end,
            defaults={'status': Appointment.Status.SCHEDULED},
        )

        self.stdout.write(self.style.SUCCESS('Sample data created or updated.'))
        self.stdout.write(
            f'Login: {username} / {password} (Admin group if available)'
        )
