import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .admin import AppointmentAdmin
from .booking import build_available_slots
from .models import Appointment, AppointmentType, Clinic, ClinicSubscription, PayPalWebhookEvent, Patient, Plan, Staff
from .tasks import send_upcoming_appointment_reminders

User = get_user_model()


class AppointmentModelTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(name='Test Clinic', timezone='UTC')
        self.user = User.objects.create_user(username='staffuser', password='password')
        self.staff = Staff.objects.create(user=self.user, clinic=self.clinic)
        self.patient = Patient.objects.create(
            clinic=self.clinic,
            first_name='Pat',
            last_name='Client',
            email='pat@example.com',
            phone='555-0101',
        )

    def test_default_status(self):
        start = timezone.now() + timedelta(hours=1)
        end = start + timedelta(minutes=30)
        appt = Appointment.objects.create(
            clinic=self.clinic,
            staff=self.staff,
            patient=self.patient,
            start_at=start,
            end_at=end,
        )
        self.assertEqual(appt.status, Appointment.Status.SCHEDULED)

    def test_overlap_prevention(self):
        start = timezone.now() + timedelta(hours=1)
        end = start + timedelta(minutes=30)
        Appointment.objects.create(
            clinic=self.clinic,
            staff=self.staff,
            patient=self.patient,
            start_at=start,
            end_at=end,
        )
        overlapping = Appointment(
            clinic=self.clinic,
            staff=self.staff,
            patient=self.patient,
            start_at=start + timedelta(minutes=10),
            end_at=end + timedelta(minutes=10),
        )
        with self.assertRaises(ValidationError):
            overlapping.save()

    def test_history_tracking(self):
        patient = Patient.objects.create(
            clinic=self.clinic,
            first_name='Pat',
            last_name='History',
            email='history@example.com',
            phone='555-0199',
        )
        patient.first_name = 'Updated'
        patient.save()
        self.assertGreaterEqual(patient.history.count(), 1)


class ReminderTaskTests(TestCase):
    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        REMINDER_WINDOW_MINUTES=60,
    )
    def test_send_reminders_marks_appointment(self):
        clinic = Clinic.objects.create(name='Reminder Clinic', timezone='UTC')
        user = User.objects.create_user(username='staff2', password='password')
        staff = Staff.objects.create(user=user, clinic=clinic)
        patient = Patient.objects.create(
            clinic=clinic,
            first_name='Dana',
            last_name='Patient',
            email='dana@example.com',
            phone='555-0133',
        )
        start = timezone.now() + timedelta(minutes=30)
        end = start + timedelta(minutes=30)
        appt = Appointment.objects.create(
            clinic=clinic,
            staff=staff,
            patient=patient,
            start_at=start,
            end_at=end,
        )

        sent = send_upcoming_appointment_reminders()

        appt.refresh_from_db()
        self.assertEqual(sent, 1)
        self.assertIsNotNone(appt.reminder_sent_at)
        self.assertEqual(len(mail.outbox), 1)


class AdminScopeTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()

        self.clinic_a = Clinic.objects.create(name='Clinic A', timezone='UTC')
        self.clinic_b = Clinic.objects.create(name='Clinic B', timezone='UTC')

        self.user_a = User.objects.create_user(username='usera', password='password')
        self.staff_a = Staff.objects.create(user=self.user_a, clinic=self.clinic_a)
        self.patient_a = Patient.objects.create(
            clinic=self.clinic_a,
            first_name='A',
            last_name='Patient',
            email='a@example.com',
            phone='555-0111',
        )
        self.patient_b = Patient.objects.create(
            clinic=self.clinic_b,
            first_name='B',
            last_name='Patient',
            email='b@example.com',
            phone='555-0222',
        )
        self.user_b = User.objects.create_user(username='userb', password='password')
        self.staff_b = Staff.objects.create(user=self.user_b, clinic=self.clinic_b)
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(minutes=20)
        Appointment.objects.create(
            clinic=self.clinic_a,
            staff=self.staff_a,
            patient=self.patient_a,
            start_at=start,
            end_at=end,
        )
        Appointment.objects.create(
            clinic=self.clinic_b,
            staff=self.staff_b,
            patient=self.patient_b,
            start_at=start + timedelta(hours=2),
            end_at=end + timedelta(hours=2),
        )

    def test_admin_queryset_scoped_to_clinic(self):
        request = self.factory.get('/admin/')
        request.user = self.user_a
        admin = AppointmentAdmin(Appointment, self.admin_site)
        qs = admin.get_queryset(request)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().clinic_id, self.clinic_a.id)


class BookingViewTests(TestCase):
    def test_booking_creates_appointment(self):
        clinic = Clinic.objects.create(name='Booking Clinic', timezone='UTC')
        user = User.objects.create_user(username='bookstaff', password='password')
        staff = Staff.objects.create(user=user, clinic=clinic)
        appointment_type = AppointmentType.objects.create(
            clinic=clinic,
            name='Consult',
            duration_minutes=45,
        )

        get_response = self.client.get(reverse('clinic-booking', args=[clinic.id]))
        self.assertEqual(get_response.status_code, 200)
        form = get_response.context['form']
        slot_choices = form.fields['slot'].choices
        self.assertTrue(slot_choices)
        slot_value = slot_choices[-1][0]
        appointment_type_id = form.fields['appointment_type_id'].initial or appointment_type.id
        response = self.client.post(
            reverse('clinic-booking', args=[clinic.id]),
            data={
                'first_name': 'Jamie',
                'last_name': 'Patient',
                'email': 'jamie@example.com',
                'phone': '555-0999',
                'appointment_type_id': appointment_type_id,
                'slot': slot_value,
                'notes': 'Test booking',
            },
        )

        self.assertEqual(response.status_code, 200)
        template_names = [template.name for template in response.templates]
        self.assertIn('core/booking_success.html', template_names, template_names)
        self.assertEqual(Appointment.objects.count(), 1)
        appointment = Appointment.objects.first()
        self.assertEqual(appointment.appointment_type_id, appointment_type.id)
        self.assertIsNotNone(appointment.confirmation_code)

    def test_booking_slug_route(self):
        clinic = Clinic.objects.create(name='Slug Clinic', timezone='UTC')
        response = self.client.get(reverse('clinic-booking-slug', args=[clinic.slug]))
        self.assertEqual(response.status_code, 200)

    def test_patient_signup_slug_route(self):
        clinic = Clinic.objects.create(name='Signup Slug Clinic', timezone='UTC')
        response = self.client.get(reverse('patient-signup-slug', args=[clinic.slug]))
        self.assertEqual(response.status_code, 200)


class AppointmentLookupTests(TestCase):
    def test_lookup_by_confirmation_code(self):
        clinic = Clinic.objects.create(name='Lookup Clinic', timezone='UTC')
        user = User.objects.create_user(username='lookupstaff', password='password')
        staff = Staff.objects.create(user=user, clinic=clinic)
        patient = Patient.objects.create(
            clinic=clinic,
            first_name='Lookup',
            last_name='Patient',
            email='lookup@example.com',
            phone='555-0333',
        )
        start = timezone.now() + timedelta(days=1)
        end = start + timedelta(minutes=30)
        appointment = Appointment.objects.create(
            clinic=clinic,
            staff=staff,
            patient=patient,
            start_at=start,
            end_at=end,
        )

        response = self.client.post(
            reverse('appointment-lookup'),
            data={'email': 'lookup@example.com', 'confirmation_code': appointment.confirmation_code},
        )

        self.assertContains(response, 'Appointment details')


class SubscriptionGateTests(TestCase):
    @override_settings(ENFORCE_SUBSCRIPTION=True)
    def test_booking_requires_active_subscription(self):
        clinic = Clinic.objects.create(name='Gate Clinic', timezone='UTC')
        user = User.objects.create_user(username='gateuser', password='password')
        Staff.objects.create(user=user, clinic=clinic)

        response = self.client.get(reverse('clinic-booking', args=[clinic.id]))
        self.assertContains(response, 'Booking temporarily unavailable')

        plan = Plan.objects.create(
            name='Basic',
            paypal_plan_id='P-TEST123',
            interval=Plan.Interval.MONTH,
            price_cents=1000,
        )
        ClinicSubscription.objects.create(
            clinic=clinic,
            plan=plan,
            paypal_subscription_id='I-TEST123',
            status=ClinicSubscription.Status.ACTIVE,
        )

        response = self.client.get(reverse('clinic-booking', args=[clinic.id]))
        self.assertEqual(response.status_code, 200)


class BillingActivateTests(TestCase):
    def test_billing_activate_creates_subscription(self):
        clinic = Clinic.objects.create(name='Billing Clinic', timezone='UTC')
        user = User.objects.create_user(username='billinguser', password='password')
        staff = Staff.objects.create(user=user, clinic=clinic)
        admin_group, _ = Group.objects.get_or_create(name='Admin')
        user.groups.add(admin_group)

        plan = Plan.objects.create(
            name='Pro',
            paypal_plan_id='P-PRO123',
            interval=Plan.Interval.MONTH,
            price_cents=2500,
        )

        self.client.force_login(user)
        response = self.client.post(
            reverse('billing-activate'),
            data=json.dumps({'plan_id': plan.id, 'subscription_id': 'I-PRO123'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(ClinicSubscription.objects.filter(paypal_subscription_id='I-PRO123').exists())


class PayPalWebhookTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(name='Webhook Clinic', timezone='UTC')
        self.plan = Plan.objects.create(
            name='Starter',
            paypal_plan_id='P-WEBHOOK1',
            interval=Plan.Interval.MONTH,
            price_cents=2500,
        )

    @patch('core.views.verify_webhook_signature', return_value=True)
    def test_webhook_is_idempotent(self, verify_mock):
        subscription = ClinicSubscription.objects.create(
            clinic=self.clinic,
            plan=self.plan,
            paypal_subscription_id='I-WEBHOOK-1',
            status=ClinicSubscription.Status.PENDING,
        )
        payload = {
            'id': 'WH-1',
            'event_type': 'BILLING.SUBSCRIPTION.ACTIVATED',
            'resource': {
                'id': subscription.paypal_subscription_id,
                'status': 'ACTIVE',
                'plan_id': self.plan.paypal_plan_id,
                'start_time': '2026-03-21T12:00:00Z',
                'billing_info': {
                    'next_billing_time': '2026-04-21T12:00:00Z',
                },
            },
        }

        first = self.client.post(
            reverse('paypal-webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        second = self.client.post(
            reverse('paypal-webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        subscription.refresh_from_db()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(verify_mock.call_count, 2)
        self.assertEqual(subscription.status, ClinicSubscription.Status.ACTIVE)
        self.assertEqual(PayPalWebhookEvent.objects.count(), 1)
        self.assertEqual(
            PayPalWebhookEvent.objects.get().status,
            PayPalWebhookEvent.ProcessingStatus.PROCESSED,
        )

    @patch('core.views.verify_webhook_signature', return_value=True)
    def test_webhook_can_create_subscription_from_custom_id(self, verify_mock):
        payload = {
            'id': 'WH-2',
            'event_type': 'BILLING.SUBSCRIPTION.ACTIVATED',
            'resource': {
                'id': 'I-WEBHOOK-2',
                'status': 'ACTIVE',
                'plan_id': self.plan.paypal_plan_id,
                'custom_id': f'clinic-{self.clinic.id}',
                'start_time': '2026-03-21T12:00:00Z',
                'billing_info': {
                    'next_billing_time': '2026-04-21T12:00:00Z',
                },
            },
        }

        response = self.client.post(
            reverse('paypal-webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(verify_mock.called)
        subscription = ClinicSubscription.objects.get(paypal_subscription_id='I-WEBHOOK-2')
        self.assertEqual(subscription.clinic_id, self.clinic.id)
        self.assertEqual(subscription.plan_id, self.plan.id)
        self.assertEqual(subscription.status, ClinicSubscription.Status.ACTIVE)
        self.assertEqual(PayPalWebhookEvent.objects.count(), 1)


class ClinicSignupTests(TestCase):
    def test_signup_creates_clinic_and_user(self):
        Plan.objects.create(
            name='Basic',
            paypal_plan_id='P-BASIC',
            interval=Plan.Interval.MONTH,
            price_cents=1000,
        )
        response = self.client.post(
            reverse('clinic-signup'),
            data={
                'clinic_name': 'Signup Clinic',
                'timezone': 'UTC',
                'admin_first_name': 'Alex',
                'admin_last_name': 'Admin',
                'admin_email': 'alex@example.com',
                'password': 'password123',
                'confirm_password': 'password123',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Clinic.objects.filter(name='Signup Clinic').exists())
        self.assertTrue(User.objects.filter(username='alex@example.com').exists())
