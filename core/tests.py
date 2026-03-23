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
from .models import (
    Appointment,
    AppointmentType,
    Clinic,
    ClinicSubscription,
    Notification,
    PayPalWebhookEvent,
    Patient,
    Plan,
    Staff,
)
from .notifications import create_clinic_notifications
from .tasks import send_upcoming_appointment_reminders
from .views import _notify_clinic_service_change

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
        self.assertEqual(Notification.objects.count(), 1)
        notification = Notification.objects.get()
        self.assertEqual(notification.recipient_id, user.id)
        self.assertEqual(notification.event_type, Notification.EventType.ONLINE_BOOKING_CREATED)
        self.assertEqual(notification.link, reverse('staff-appointment-edit', args=[appointment.id]))

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
        self.assertEqual(Notification.objects.filter(recipient=user).count(), 1)
        notification = Notification.objects.get(recipient=user)
        self.assertEqual(notification.link, reverse('billing'))
        self.assertIn(notification.title, {'Subscription activation recorded', 'Subscription activated'})


class FreemiumPlanTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(name='Freemium Clinic', timezone='UTC')
        self.admin_user = User.objects.create_user(
            username='freemium-admin@example.com',
            email='freemium-admin@example.com',
            password='password',
        )
        admin_group, _ = Group.objects.get_or_create(name='Admin')
        self.admin_user.groups.add(admin_group)
        self.admin_staff = Staff.objects.create(user=self.admin_user, clinic=self.clinic)

    def _activate_plan(self, plan):
        return ClinicSubscription.objects.create(
            clinic=self.clinic,
            plan=plan,
            paypal_subscription_id=f'LOCAL-{self.clinic.id}-{plan.id}',
            status=ClinicSubscription.Status.ACTIVE,
        )

    def test_signup_activate_supports_free_plan_without_paypal(self):
        free_plan = Plan.objects.create(
            name='Free Trial',
            is_free=True,
            price_cents=0,
            interval=Plan.Interval.MONTH,
            staff_limit=2,
            service_limit=3,
            monthly_appointment_limit=50,
            includes_reminders=False,
        )
        session = self.client.session
        session['signup_clinic_id'] = self.clinic.id
        session.save()

        response = self.client.post(
            reverse('signup-activate'),
            data=json.dumps({'clinic_id': self.clinic.id, 'plan_id': free_plan.id}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['is_free'])
        subscription = ClinicSubscription.objects.get(clinic=self.clinic, plan=free_plan)
        self.assertEqual(subscription.status, ClinicSubscription.Status.ACTIVE)
        self.assertTrue(subscription.paypal_subscription_id.startswith('LOCAL-'))

    def test_staff_creation_blocks_when_free_limit_is_reached(self):
        free_plan = Plan.objects.create(
            name='Free Seats',
            is_free=True,
            price_cents=0,
            interval=Plan.Interval.MONTH,
            staff_limit=1,
            service_limit=3,
            monthly_appointment_limit=50,
        )
        self._activate_plan(free_plan)
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse('staff-members'),
            data={
                'add-email': 'newstaff@example.com',
                'add-first_name': 'New',
                'add-last_name': 'Staff',
                'add-password': 'password123',
                'add-role': 'Doctor',
                'add-is_active': 'on',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Free plan limit reached')
        self.assertEqual(Staff.objects.filter(clinic=self.clinic).count(), 1)

    def test_public_booking_blocks_when_free_monthly_limit_is_reached(self):
        free_plan = Plan.objects.create(
            name='Free Visits',
            is_free=True,
            price_cents=0,
            interval=Plan.Interval.MONTH,
            staff_limit=2,
            service_limit=3,
            monthly_appointment_limit=1,
        )
        self._activate_plan(free_plan)
        appointment_type = AppointmentType.objects.create(
            clinic=self.clinic,
            name='Consult',
            duration_minutes=30,
        )
        patient = Patient.objects.create(
            clinic=self.clinic,
            first_name='Existing',
            last_name='Patient',
            email='existing@example.com',
            phone='555-0191',
        )
        start = timezone.now() + timedelta(days=1)
        Appointment.objects.create(
            clinic=self.clinic,
            appointment_type=appointment_type,
            staff=self.admin_staff,
            patient=patient,
            start_at=start,
            end_at=start + timedelta(minutes=30),
        )

        get_response = self.client.get(reverse('clinic-booking', args=[self.clinic.id]))
        slot_value = get_response.context['form'].fields['slot'].choices[-1][0]
        response = self.client.post(
            reverse('clinic-booking', args=[self.clinic.id]),
            data={
                'first_name': 'Jamie',
                'last_name': 'Patient',
                'email': 'jamie@example.com',
                'phone': '555-0999',
                'appointment_type_id': appointment_type.id,
                'slot': slot_value,
                'notes': 'Blocked by free limit',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Free plan limit reached')
        self.assertEqual(Appointment.objects.filter(clinic=self.clinic).count(), 1)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        REMINDER_WINDOW_MINUTES=60,
    )
    def test_reminders_skip_when_plan_disables_them(self):
        free_plan = Plan.objects.create(
            name='Free No Reminders',
            is_free=True,
            price_cents=0,
            interval=Plan.Interval.MONTH,
            staff_limit=2,
            service_limit=3,
            monthly_appointment_limit=50,
            includes_reminders=False,
        )
        self._activate_plan(free_plan)
        patient = Patient.objects.create(
            clinic=self.clinic,
            first_name='Dana',
            last_name='Patient',
            email='dana-free@example.com',
            phone='555-0133',
        )
        start = timezone.now() + timedelta(minutes=30)
        Appointment.objects.create(
            clinic=self.clinic,
            staff=self.admin_staff,
            patient=patient,
            start_at=start,
            end_at=start + timedelta(minutes=30),
        )

        sent = send_upcoming_appointment_reminders()

        self.assertEqual(sent, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_notifications_skip_when_plan_disables_them(self):
        free_plan = Plan.objects.create(
            name='Free Quiet',
            is_free=True,
            price_cents=0,
            interval=Plan.Interval.MONTH,
            staff_limit=2,
            service_limit=3,
            monthly_appointment_limit=50,
            includes_notifications=False,
        )
        self._activate_plan(free_plan)

        create_clinic_notifications(
            self.clinic,
            title='Quiet notification',
            body='Should not persist on this plan.',
        )

        self.assertEqual(Notification.objects.filter(clinic=self.clinic).count(), 0)


class NotificationCenterTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(name='Notify Clinic', timezone='UTC')
        self.user = User.objects.create_user(username='notifyadmin', password='password')
        admin_group, _ = Group.objects.get_or_create(name='Admin')
        self.user.groups.add(admin_group)
        self.staff = Staff.objects.create(user=self.user, clinic=self.clinic)
        self.frontdesk_user = User.objects.create_user(username='notifyfrontdesk', password='password')
        frontdesk_group, _ = Group.objects.get_or_create(name='FrontDesk')
        self.frontdesk_user.groups.add(frontdesk_group)
        self.frontdesk_staff = Staff.objects.create(user=self.frontdesk_user, clinic=self.clinic)
        self.patient = Patient.objects.create(
            clinic=self.clinic,
            first_name='Jamie',
            last_name='Patient',
            email='notify-patient@example.com',
            phone='555-0811',
        )
        start = timezone.now() + timedelta(days=1)
        self.appointment = Appointment.objects.create(
            clinic=self.clinic,
            staff=self.staff,
            patient=self.patient,
            start_at=start,
            end_at=start + timedelta(minutes=30),
        )
        self.client.force_login(self.user)

    def test_notifications_page_and_mark_read_actions(self):
        unread_one = Notification.objects.create(
            clinic=self.clinic,
            recipient=self.user,
            event_type=Notification.EventType.STAFF_ADDED,
            level=Notification.Level.SUCCESS,
            title='Staff added',
            body='A new staff member joined the clinic.',
            link=reverse('staff-members'),
        )
        unread_two = Notification.objects.create(
            clinic=self.clinic,
            recipient=self.user,
            event_type=Notification.EventType.SERVICE_UPDATED,
            level=Notification.Level.INFO,
            title='Service updated',
            body='General checkup duration changed.',
            link=reverse('appointment-types'),
        )
        read_notification = Notification.objects.create(
            clinic=self.clinic,
            recipient=self.user,
            event_type=Notification.EventType.GENERIC,
            level=Notification.Level.INFO,
            title='Older message',
            body='This one is already read.',
            is_read=True,
            read_at=timezone.now(),
        )

        response = self.client.get(reverse('notifications'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unread_one.title)
        self.assertContains(response, unread_two.title)
        self.assertContains(response, read_notification.title)
        self.assertEqual(response.context['unread_total'], 2)
        self.assertEqual(response.context['total_notifications'], 3)

        mark_one = self.client.post(
            reverse('notification-mark-read', args=[unread_one.id]),
            data={'next': reverse('notifications')},
        )
        self.assertEqual(mark_one.status_code, 302)
        unread_one.refresh_from_db()
        self.assertTrue(unread_one.is_read)

        mark_all = self.client.post(
            reverse('notifications-mark-all-read'),
            data={'next': reverse('notifications')},
        )
        self.assertEqual(mark_all.status_code, 302)
        unread_two.refresh_from_db()
        self.assertTrue(unread_two.is_read)
        self.assertEqual(Notification.objects.filter(recipient=self.user, is_read=False).count(), 0)

    def test_open_notification_marks_it_read_and_redirects(self):
        notification = Notification.objects.create(
            clinic=self.clinic,
            recipient=self.user,
            event_type=Notification.EventType.APPOINTMENT_CREATED,
            level=Notification.Level.SUCCESS,
            title='Appointment added',
            body='A new appointment was booked.',
            link=reverse('staff-appointments'),
        )

        response = self.client.get(
            f"{reverse('notification-open', args=[notification.id])}?next={reverse('staff-appointments')}"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('staff-appointments'))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_frontdesk_notification_open_redirects_to_appointment_detail(self):
        notification = Notification.objects.create(
            clinic=self.clinic,
            recipient=self.frontdesk_user,
            event_type=Notification.EventType.APPOINTMENT_CREATED,
            level=Notification.Level.SUCCESS,
            title='Appointment added',
            body='A new appointment was booked.',
            link=reverse('staff-appointments'),
            metadata={
                'appointment_id': self.appointment.id,
                'patient_id': self.patient.id,
                'staff_id': self.staff.id,
            },
        )
        self.client.force_login(self.frontdesk_user)

        response = self.client.get(
            f"{reverse('notification-open', args=[notification.id])}?next={reverse('staff-appointments')}"
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('staff-appointment-edit', args=[self.appointment.id]))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_service_notifications_are_admin_only(self):
        appointment_type = AppointmentType.objects.create(
            clinic=self.clinic,
            name='General checkup',
            duration_minutes=30,
        )

        _notify_clinic_service_change(
            clinic=self.clinic,
            appointment_type=appointment_type,
            actor=self.user,
            created=False,
        )

        self.assertEqual(Notification.objects.filter(recipient=self.user).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.frontdesk_user).count(), 0)


class PayPalWebhookTests(TestCase):
    def setUp(self):
        self.clinic = Clinic.objects.create(name='Webhook Clinic', timezone='UTC')
        self.plan = Plan.objects.create(
            name='Starter',
            paypal_plan_id='P-WEBHOOK1',
            interval=Plan.Interval.MONTH,
            price_cents=2500,
        )
        self.admin_user = User.objects.create_user(username='webhookadmin', password='password')
        admin_group, _ = Group.objects.get_or_create(name='Admin')
        self.admin_user.groups.add(admin_group)
        self.admin_staff = Staff.objects.create(user=self.admin_user, clinic=self.clinic)

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

        with self.captureOnCommitCallbacks(execute=True):
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
        self.assertEqual(Notification.objects.filter(recipient=self.admin_user).count(), 1)
        notification = Notification.objects.get(recipient=self.admin_user)
        self.assertEqual(notification.title, 'Subscription activated')
        self.assertEqual(notification.event_type, Notification.EventType.SUBSCRIPTION_ACTIVATED)

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

        with self.captureOnCommitCallbacks(execute=True):
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
        self.assertEqual(Notification.objects.filter(recipient=self.admin_user).count(), 1)


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
