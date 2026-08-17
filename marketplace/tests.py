import json
import os
import subprocess
import sys
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone as django_timezone

from .forms import ChangePasswordForm, MarketOrderForm, ServiceAreaForm
from .models import (
    BusinessProfile,
    Invoice,
    InvoiceLine,
    MarketListing,
    MarketOrder,
    Message,
    PlatformCircumventionCase,
    PlatformFee,
    PlatformSettings,
    PromoCode,
    PublicReview,
    Quote,
    QuotingAppointment,
    QuotingAppointmentSlot,
    SocialDataDeletionRequest,
    SocialIdentity,
    Task,
    TradieProfile,
    User,
    UserCapability,
    Workspace,
    WorkspaceMembership,
)
from . import meta_client, workspaces
from .utils import (
    calculate_market_price_per_unit,
    calculate_market_take_home,
    calculate_platform_fee,
    create_platform_fee_for_task,
    send_invoice_notifications,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class CriticalPathViewsTests(TestCase):
    def test_home_page_returns_200(self):
        response = self.client.get('/', secure=True)
        self.assertEqual(response.status_code, 200)

    def test_healthz_returns_200_when_db_is_available(self):
        response = self.client.get('/healthz/', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_healthz_returns_503_when_db_unavailable(self):
        with mock.patch('django.db.connection.cursor', side_effect=DatabaseError('Database connection unavailable')):
            response = self.client.get('/healthz/', secure=True)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'status': 'error'})

    def test_admin_login_page_is_reachable(self):
        response = self.client.get('/admin/login/', secure=True)
        self.assertEqual(response.status_code, 200)


class ProductionSettingsTests(TestCase):
    @staticmethod
    def _base_production_env():
        return {
            'DJANGO_ENV': 'production',
            'DEBUG': 'False',
            'SECRET_KEY': 'x' * 64,
            'ALLOWED_HOSTS': 'example.com',
            'CSRF_TRUSTED_ORIGINS': 'https://example.com',
            'OBJECT_STORAGE_BACKEND': 's3',
            'AWS_STORAGE_BUCKET_NAME': 'bucket-name',
            'AWS_ACCESS_KEY_ID': 'access-key',
            'AWS_SECRET_ACCESS_KEY': 'secret-key',
            'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'EMAIL_HOST': 'smtp.example.com',
            'EMAIL_HOST_USER': 'apikey',
            'EMAIL_HOST_PASSWORD': 'secret',
        }

    @staticmethod
    def _run_settings_import(extra_env):
        env = {
            'PATH': os.environ.get('PATH', ''),
            'PYTHONPATH': os.environ.get('PYTHONPATH', ''),
            'PYTHONHOME': os.environ.get('PYTHONHOME', ''),
            'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''),
        }
        env.update(extra_env)
        return subprocess.run(
            [sys.executable, '-c', 'import coconut_wireless.settings'],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_production_requires_debug_false(self):
        result = self._run_settings_import(
            {
                'DJANGO_ENV': 'production',
                'DEBUG': 'True',
                'SECRET_KEY': 'x' * 64,
                'ALLOWED_HOSTS': 'example.com',
                'CSRF_TRUSTED_ORIGINS': 'https://example.com',
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DEBUG must be False', result.stderr)

    def test_production_requires_secret_key(self):
        result = self._run_settings_import(
            {
                'DJANGO_ENV': 'production',
                'DEBUG': 'False',
                'SECRET_KEY': '',
                'ALLOWED_HOSTS': 'example.com',
                'CSRF_TRUSTED_ORIGINS': 'https://example.com',
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY environment variable is required', result.stderr)

    def test_production_requires_allowed_hosts(self):
        result = self._run_settings_import(
            {
                'DJANGO_ENV': 'production',
                'DEBUG': 'False',
                'SECRET_KEY': 'x' * 64,
                'ALLOWED_HOSTS': '',
                'CSRF_TRUSTED_ORIGINS': 'https://example.com',
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ALLOWED_HOSTS must be set', result.stderr)

    def test_production_requires_csrf_trusted_origins(self):
        result = self._run_settings_import(
            {
                'DJANGO_ENV': 'production',
                'DEBUG': 'False',
                'SECRET_KEY': 'x' * 64,
                'ALLOWED_HOSTS': 'example.com',
                'CSRF_TRUSTED_ORIGINS': '',
            }
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('CSRF_TRUSTED_ORIGINS must be set', result.stderr)

    def test_production_rejects_weak_secret_key(self):
        env = self._base_production_env()
        env['SECRET_KEY'] = 'weak-key'
        result = self._run_settings_import(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SECRET_KEY must be at least 50 characters', result.stderr)

    def test_production_secure_defaults(self):
        env = os.environ.copy()
        env.update(self._base_production_env())
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                (
                    'import json;'
                    'import coconut_wireless.settings as s;'
                    'print(json.dumps({'
                    '"SESSION_COOKIE_SECURE": s.SESSION_COOKIE_SECURE,'
                    '"CSRF_COOKIE_SECURE": s.CSRF_COOKIE_SECURE,'
                    '"SECURE_SSL_REDIRECT": s.SECURE_SSL_REDIRECT,'
                    '"SECURE_HSTS_SECONDS": s.SECURE_HSTS_SECONDS'
                    '}))'
                ),
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        payload = json.loads(result.stdout.strip())
        self.assertTrue(payload['SESSION_COOKIE_SECURE'])
        self.assertTrue(payload['CSRF_COOKIE_SECURE'])
        self.assertTrue(payload['SECURE_SSL_REDIRECT'])
        self.assertGreater(payload['SECURE_HSTS_SECONDS'], 0)

    def test_production_requires_object_storage_backend(self):
        env = self._base_production_env()
        env['OBJECT_STORAGE_BACKEND'] = 'filesystem'
        result = self._run_settings_import(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OBJECT_STORAGE_BACKEND must be set to "s3"', result.stderr)

    def test_production_requires_smtp_configuration(self):
        env = self._base_production_env()
        env['EMAIL_HOST'] = ''
        result = self._run_settings_import(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing required SMTP environment variables in production', result.stderr)

    def test_s3_endpoint_builds_media_url(self):
        env = os.environ.copy()
        base = self._base_production_env()
        base['AWS_S3_ENDPOINT_URL'] = 'https://r2.example.com'
        env.update(base)
        result = subprocess.run(
            [
                sys.executable,
                '-c',
                'import coconut_wireless.settings as s; print(s.MEDIA_URL)',
            ],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout.strip(), 'https://r2.example.com/bucket-name/')


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class ClosedBetaAndApprovalFlowTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email='client@example.com',
            password='pass12345',
            first_name='Client',
            last_name='User',
            role=User.ROLE_CLIENT,
            town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='tradie@example.com',
            password='pass12345',
            first_name='Tradie',
            last_name='User',
            role=User.ROLE_TRADIE,
            town='Suva',
        )
        self.tradie_profile = TradieProfile.objects.create(
            user=self.tradie_user,
            trades=['cleaning'],
            service_towns=['Suva'],
            verification_status=TradieProfile.VERIFICATION_PENDING,
        )
        self.task = Task.objects.create(
            client=self.client_user,
            title='Fix sink',
            category='plumbing',
            description='Kitchen sink leaking',
            budget=Decimal('150.00'),
            town='Suva',
        )

    @override_settings(
        BETA_GATE_CLIENT_SIGNUPS=True,
        BETA_ALLOWED_EMAILS={'invitee@example.com'},
        BETA_ALLOWED_DOMAINS=set(),
    )
    def test_client_registration_requires_invited_email(self):
        response = self.client.post(
            reverse('register_client'),
            {
                'first_name': 'New',
                'last_name': 'Client',
                'email': 'blocked@example.com',
                'mobile': '+679 123 4567',
                'town': 'Suva',
                'password': 'pass12345',
                'password_confirm': 'pass12345',
                'accepted_terms': 'on',
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invite-only for closed beta')

    @override_settings(
        BETA_GATE_TRADIE_SIGNUPS=True,
        BETA_ALLOWED_EMAILS={'invited-tradie@example.com'},
        BETA_ALLOWED_DOMAINS=set(),
    )
    def test_tradie_registration_with_documents_starts_pending(self):
        response = self.client.post(
            reverse('register_tradie'),
            {
                'first_name': 'Invited',
                'last_name': 'Tradie',
                'email': 'invited-tradie@example.com',
                'mobile': '+679 111 2222',
                'town': 'Suva',
                'password': 'pass12345',
                'password_confirm': 'pass12345',
                'business_name': 'Invited Services',
                'tin': 'P123',
                'years_experience': '1-3 years',
                'bio': 'Experienced tradie',
                'trades': ['cleaning'],
                'service_towns': ['Suva'],
                'accepted_terms': 'on',
                'accepted_platform_circumvention': 'on',
                'accepted_invoicing_terms': 'on',
                'tin_letter': SimpleUploadedFile('tin.pdf', b'pdf-content', content_type='application/pdf'),
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='invited-tradie@example.com')
        self.assertEqual(user.tradie_profile.verification_status, TradieProfile.VERIFICATION_PENDING)

    def test_tradie_can_register_without_a_business_name(self):
        # Individual contractors don't necessarily operate under a
        # registered business/company name — business_name is optional.
        response = self.client.post(
            reverse('register_tradie'),
            {
                'first_name': 'Solo',
                'last_name': 'Contractor',
                'email': 'solo-contractor@example.com',
                'mobile': '+679 111 2222',
                'town': 'Suva',
                'password': 'pass12345',
                'password_confirm': 'pass12345',
                'business_name': '',
                'tin': '',
                'years_experience': '1-3 years',
                'bio': 'Independent contractor',
                'trades': ['cleaning'],
                'service_towns': ['Suva'],
                'accepted_terms': 'on',
                'accepted_platform_circumvention': 'on',
                'accepted_invoicing_terms': 'on',
                'tin_letter': SimpleUploadedFile('tin.pdf', b'pdf-content', content_type='application/pdf'),
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='solo-contractor@example.com')
        self.assertEqual(user.tradie_profile.business_name, '')
        self.assertFalse(user.tradie_profile.documents_verified)

    def test_pending_tradie_can_submit_quote(self):
        # Pending tradies may browse and quote while awaiting verification —
        # clients see a "Pending verification" badge instead of being blocked.
        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.post(
            reverse('submit_quote', args=[self.task.pk]),
            {
                'price': '120.00',
                'message': 'Can complete this week',
                'quote_includes': 'labour_only',
            },
            secure=True,
        )
        self.assertRedirects(response, reverse('task_detail', args=[self.task.pk]), fetch_redirect_response=False)
        self.assertTrue(Quote.objects.filter(task=self.task, tradie=self.tradie_user).exists())

    def test_rejected_tradie_cannot_submit_quote(self):
        self.tradie_profile.verification_status = TradieProfile.VERIFICATION_REJECTED
        self.tradie_profile.save()
        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.post(
            reverse('submit_quote', args=[self.task.pk]),
            {
                'price': '120.00',
                'message': 'Can complete this week',
                'quote_includes': 'labour_only',
            },
            secure=True,
        )
        self.assertRedirects(response, reverse('tradie_dashboard'), fetch_redirect_response=False)
        self.assertFalse(Quote.objects.filter(task=self.task, tradie=self.tradie_user).exists())

    def test_approved_tradie_can_submit_quote(self):
        self.tradie_profile.verification_status = TradieProfile.VERIFICATION_APPROVED
        self.tradie_profile.save()
        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.post(
            reverse('submit_quote', args=[self.task.pk]),
            {
                'price': '120.00',
                'message': 'Can complete this week',
                'quote_includes': 'labour_only',
            },
            secure=True,
        )
        self.assertRedirects(response, reverse('task_detail', args=[self.task.pk]), fetch_redirect_response=False)
        self.assertTrue(Quote.objects.filter(task=self.task, tradie=self.tradie_user).exists())

    def test_core_task_quote_accept_complete_flow(self):
        self.tradie_profile.verification_status = TradieProfile.VERIFICATION_APPROVED
        self.tradie_profile.save()

        self.client.login(username=self.client_user.email, password='pass12345')
        post_response = self.client.post(
            reverse('post_task'),
            {
                'title': 'Install light fitting',
                'category': 'electrical',
                'description': 'Replace kitchen pendant light',
                'budget': '220.00',
                'town': 'Suva',
                'urgency': 'this_week',
                'budget_type': 'fixed',
            },
            secure=True,
        )
        self.assertEqual(post_response.status_code, 302)
        posted_task = Task.objects.get(title='Install light fitting')

        self.client.logout()
        self.client.login(username=self.tradie_user.email, password='pass12345')
        quote_response = self.client.post(
            reverse('submit_quote', args=[posted_task.pk]),
            {
                'price': '210.00',
                'message': 'Available tomorrow',
                'quote_includes': 'labour_only',
            },
            secure=True,
        )
        self.assertEqual(quote_response.status_code, 302)
        quote = Quote.objects.get(task=posted_task, tradie=self.tradie_user)

        self.client.logout()
        self.client.login(username=self.client_user.email, password='pass12345')
        accept_response = self.client.post(reverse('accept_quote', args=[posted_task.pk, quote.pk]), secure=True)
        self.assertEqual(accept_response.status_code, 302)
        posted_task.refresh_from_db()
        self.assertEqual(posted_task.status, Task.STATUS_ASSIGNED)
        self.assertEqual(posted_task.assigned_tradie, self.tradie_user)

        complete_response = self.client.post(reverse('complete_task', args=[posted_task.pk]), secure=True)
        self.assertEqual(complete_response.status_code, 302)
        posted_task.refresh_from_db()
        self.assertEqual(posted_task.status, Task.STATUS_COMPLETED)
        self.assertTrue(posted_task.platform_fees.exists())

    @mock.patch('django.core.mail.send_mail')
    def test_invoice_notification_sends_email_and_updates_status(self, send_mail_mock):
        invoice = Invoice.objects.create(
            tradie=self.tradie_user,
            invoice_number='INV-TEST-001',
            total_amount=Decimal('50.00'),
            due_date=self.task.created_at.date(),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            task=self.task,
            description='Platform fee for test task',
            amount=Decimal('50.00'),
        )

        send_invoice_notifications(invoice)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.STATUS_SENT)
        self.assertEqual(invoice.notifications.count(), 3)
        channels = set(invoice.notifications.values_list('channel', flat=True))
        self.assertEqual(channels, {'in_platform', 'email', 'sms'})
        self.assertTrue(invoice.notifications.filter(recipient=self.tradie_user, channel='in_platform').exists())
        self.assertTrue(invoice.notifications.filter(recipient=self.tradie_user, channel='email').exists())
        self.assertTrue(invoice.notifications.filter(recipient=self.tradie_user, channel='sms').exists())
        for notification in invoice.notifications.all():
            self.assertTrue(notification.body)
            self.assertEqual(notification.recipient, self.tradie_user)
        send_mail_mock.assert_called_once()


class ServiceAreaFormTests(TestCase):
    """Form-level only — avoids self.client GET (see other tests in this
    file for the established pattern of only asserting on redirects, not
    rendered template content, due to the pre-existing Python 3.14 test-
    instrumentation issue documented in FUTURE_IMPLEMENTATION_STATUS.md)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='areatest@example.com', password='pass12345',
            first_name='Area', last_name='Test', role=User.ROLE_TRADIE, town='Suva',
        )
        self.profile = TradieProfile.objects.create(user=self.user, trades=['cleaning'], service_towns=['Suva'])

    def test_multiple_towns_is_valid_and_saves_as_a_list(self):
        form = ServiceAreaForm({'service_towns': ['Suva', 'Nadi', 'Labasa']}, instance=self.profile)
        self.assertTrue(form.is_valid())
        form.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.service_towns, ['Suva', 'Nadi', 'Labasa'])

    def test_no_towns_selected_is_invalid(self):
        form = ServiceAreaForm({'service_towns': []}, instance=self.profile)
        self.assertFalse(form.is_valid())
        self.assertIn('service_towns', form.errors)


class ServiceAreaViewTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email='client-area@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='tradie-area@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        self.profile = TradieProfile.objects.create(user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'])

    def test_tradie_can_update_service_towns(self):
        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.post(
            reverse('edit_service_area'),
            {'service_towns': ['Suva', 'Nadi']},
            secure=True,
        )
        self.assertRedirects(response, reverse('tradie_dashboard'), fetch_redirect_response=False)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.service_towns, ['Suva', 'Nadi'])

    def test_client_role_cannot_update_service_towns(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.post(
            reverse('edit_service_area'),
            {'service_towns': ['Suva', 'Nadi']},
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.service_towns, ['Suva'])


class QuotingAppointmentSecurityTests(TestCase):
    """Covers the fix for state-mutating appointment actions previously
    being reachable via plain GET (no @require_POST), which meant a link
    prefetch/crawler could trigger them without tripping CSRF checks
    (Django's CSRF middleware only gates unsafe methods)."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email='appt-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='appt-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        TradieProfile.objects.create(
            user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'],
            verification_status=TradieProfile.VERIFICATION_APPROVED,
        )
        self.task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Kitchen sink leaking', budget=Decimal('150.00'), town='Suva',
        )
        self.appointment = QuotingAppointment.objects.create(
            task=self.task, client=self.client_user, provider=self.tradie_user, status=QuotingAppointment.STATUS_REQUESTED,
        )
        self.slot = QuotingAppointmentSlot.objects.create(
            quoting_appointment=self.appointment,
            proposed_date='2026-08-01', start_time='09:00', end_time='10:00',
        )

    def test_accept_slot_rejects_get(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.get(
            reverse('accept_quoting_appointment_slot', args=[self.task.pk, self.appointment.pk, self.slot.pk]),
            secure=True,
        )
        self.assertEqual(response.status_code, 405)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, QuotingAppointment.STATUS_REQUESTED)

    def test_accept_slot_still_works_via_post(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.post(
            reverse('accept_quoting_appointment_slot', args=[self.task.pk, self.appointment.pk, self.slot.pk]),
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, QuotingAppointment.STATUS_ACCEPTED)

    def test_decline_appointment_rejects_get(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.get(
            reverse('decline_quoting_appointment', args=[self.task.pk, self.appointment.pk]),
            secure=True,
        )
        self.assertEqual(response.status_code, 405)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, QuotingAppointment.STATUS_REQUESTED)

    def test_cancel_appointment_rejects_get(self):
        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.get(
            reverse('cancel_quoting_appointment', args=[self.task.pk, self.appointment.pk]),
            secure=True,
        )
        self.assertEqual(response.status_code, 405)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, QuotingAppointment.STATUS_REQUESTED)


class ConversationAccessTests(TestCase):
    """Covers the fix for conversation() only checking the requester is a
    task party, never the other end — previously a logged-in user could
    inject a message into a total stranger's inbox by guessing a task pk
    and user pk in /messages/<tpk>/<opk>/."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email='conv-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='conv-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        self.stranger = User.objects.create_user(
            email='conv-stranger@example.com', password='pass12345',
            first_name='Stranger', last_name='User', role=User.ROLE_CLIENT, town='Nadi',
        )
        self.task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Kitchen sink leaking', budget=Decimal('150.00'), town='Suva',
        )
        Quote.objects.create(task=self.task, tradie=self.tradie_user, price=Decimal('120.00'), message='Can do it', quote_includes='labour_only')

    def test_task_client_cannot_message_a_stranger_to_the_task(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.post(
            reverse('conversation', args=[self.task.pk, self.stranger.pk]),
            {'body': 'hello'},
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Message.objects.filter(task=self.task, recipient=self.stranger).exists())

    def test_stranger_cannot_open_conversation_between_others(self):
        self.client.login(username=self.stranger.email, password='pass12345')
        response = self.client.post(
            reverse('conversation', args=[self.task.pk, self.tradie_user.pk]),
            {'body': 'hello'},
            secure=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_client_can_still_message_the_quoting_tradie(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.post(
            reverse('conversation', args=[self.task.pk, self.tradie_user.pk]),
            {'body': 'hello'},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Message.objects.filter(task=self.task, sender=self.client_user, recipient=self.tradie_user).exists())


class TradieProfileDoesNotExposeDocumentsTests(TestCase):
    """Rendering tradie_profile.html via self.client crashes in this local
    Python 3.14 dev environment (see FUTURE_IMPLEMENTATION_STATUS.md /
    other tests in this file for the same, pre-existing limitation), so
    this is a static regression guard on the template source instead of a
    rendered-response assertion: verification documents and the raw TIN
    number must never be linked/printed on this public-facing page."""

    def test_template_does_not_link_verification_documents_or_tin(self):
        template_path = (
            PROJECT_ROOT / 'marketplace' / 'templates' / 'marketplace' / 'tradie_profile.html'
        )
        content = template_path.read_text(encoding='utf-8')
        for forbidden in (
            'tin_letter.url', 'business_licence.url', 'public_liability_insurance.url',
            'electrical_contractors_licence.url', 'plumber_licence.url', 'profile.tin }}',
        ):
            self.assertNotIn(forbidden, content, f'{forbidden!r} must not appear in tradie_profile.html — it is a public page.')


class TradieDashboardMissingProfileTests(TestCase):
    def test_redirects_to_contact_support_instead_of_crashing(self):
        user = User.objects.create_user(
            email='noprofile@example.com', password='pass12345',
            first_name='No', last_name='Profile', role=User.ROLE_TRADIE, town='Suva',
        )
        self.client.login(username=user.email, password='pass12345')
        response = self.client.get(reverse('tradie_dashboard'), secure=True)
        self.assertRedirects(response, reverse('contact_support'), fetch_redirect_response=False)


class MarketOrderFulfillTests(TestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email='seller@example.com', password='pass12345',
            first_name='Sell', last_name='Er', role=User.ROLE_CLIENT, town='Suva',
        )
        self.buyer = User.objects.create_user(
            email='buyer@example.com', password='pass12345',
            first_name='Buy', last_name='Er', role=User.ROLE_CLIENT, town='Suva',
        )
        self.listing = MarketListing.objects.create(
            seller=self.seller, category=MarketListing.CATEGORY_OTHER, title='Handmade baskets',
            take_home_per_unit=Decimal('10.00'), price_per_unit=Decimal('12.00'),
            fee_rate_at_listing=Decimal('7.5'), units_available=10,
            fulfillment_method=MarketListing.FULFILLMENT_PICKUP, pickup_town='Suva',
            available_dates=['2026-08-01'],
        )
        self.order = MarketOrder.objects.create(
            listing=self.listing, buyer=self.buyer, quantity=2,
            unit_price_at_order=Decimal('12.00'), total_price=Decimal('24.00'),
            platform_fee_amount=Decimal('1.80'), fulfillment_method=MarketListing.FULFILLMENT_PICKUP,
            requested_date='2026-08-01', status=MarketOrder.STATUS_ACCEPTED,
        )

    def test_seller_can_mark_accepted_order_fulfilled(self):
        self.client.login(username=self.seller.email, password='pass12345')
        response = self.client.post(reverse('market_order_respond', args=[self.order.pk, 'fulfill']), secure=True)
        self.assertRedirects(response, reverse('my_market_listings'), fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, MarketOrder.STATUS_FULFILLED)

    def test_pending_order_cannot_be_fulfilled_directly(self):
        # Uses RequestFactory + a direct view call rather than self.client:
        # Http404 renders 404.html, which hits the same pre-existing
        # Python 3.14 test-instrumentation crash as any other rendered
        # template in this local environment (see other tests in this
        # file) — calling the view directly lets Http404 propagate as a
        # plain exception instead.
        from django.http import Http404
        from django.test import RequestFactory
        from . import views as marketplace_views

        self.order.status = MarketOrder.STATUS_PENDING
        self.order.save()
        request = RequestFactory().post(reverse('market_order_respond', args=[self.order.pk, 'fulfill']))
        request.user = self.seller
        with self.assertRaises(Http404):
            marketplace_views.market_order_respond(request, self.order.pk, 'fulfill')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, MarketOrder.STATUS_PENDING)

    def test_fulfilled_order_can_no_longer_be_cancelled_by_buyer(self):
        self.order.status = MarketOrder.STATUS_FULFILLED
        self.order.save()
        self.client.login(username=self.buyer.email, password='pass12345')
        response = self.client.post(reverse('market_order_cancel', args=[self.order.pk]), secure=True)
        self.assertRedirects(response, reverse('my_market_orders'), fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, MarketOrder.STATUS_FULFILLED)


class MarketOrderDeliveryTownScopingTests(TestCase):
    def test_delivery_town_choices_are_scoped_to_listing(self):
        seller = User.objects.create_user(
            email='deliv-seller@example.com', password='pass12345',
            first_name='Sell', last_name='Er', role=User.ROLE_CLIENT, town='Suva',
        )
        listing = MarketListing.objects.create(
            seller=seller, category=MarketListing.CATEGORY_OTHER, title='Firewood bundles',
            take_home_per_unit=Decimal('5.00'), price_per_unit=Decimal('6.00'),
            fee_rate_at_listing=Decimal('7.5'), units_available=20,
            fulfillment_method=MarketListing.FULFILLMENT_DELIVERY,
            delivery_towns=['Suva', 'Nausori'], available_dates=['2026-08-01'],
        )
        form = MarketOrderForm(listing=listing)
        choice_values = [value for value, _ in form.fields['delivery_town'].choices]
        self.assertEqual(set(choice_values), {'', 'Suva', 'Nausori'})


class MarketListingClientSellerProfileLinkTests(TestCase):
    def test_client_seller_listing_page_does_not_404_on_seller_link(self):
        seller = User.objects.create_user(
            email='client-seller@example.com', password='pass12345',
            first_name='Client', last_name='Seller', role=User.ROLE_CLIENT, town='Suva',
        )
        listing = MarketListing.objects.create(
            seller=seller, category=MarketListing.CATEGORY_OTHER, title='Garden tools',
            take_home_per_unit=Decimal('8.00'), price_per_unit=Decimal('10.00'),
            fee_rate_at_listing=Decimal('7.5'), units_available=5,
            fulfillment_method=MarketListing.FULFILLMENT_PICKUP, pickup_town='Suva',
            available_dates=['2026-08-01'],
        )
        # Rendering market_listing_detail.html crashes under this local
        # environment's Python 3.14 test-instrumentation issue (see other
        # tests in this file), so this checks the template source directly:
        # the seller link must be conditional on role, not unconditional.
        template_path = PROJECT_ROOT / 'marketplace' / 'templates' / 'marketplace' / 'market_listing_detail.html'
        content = template_path.read_text(encoding='utf-8')
        self.assertIn("if listing.seller.role == 'tradie'", content)


class PaymentRestrictionEnforcementTests(TestCase):
    """utils.is_tradie_payment_restricted() already existed and billing.html
    already warned about it, but nothing actually enforced it — can_quote()/
    quote_block_reason() never called it. Wires it in."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email='pr-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='pr-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        self.tradie_profile = TradieProfile.objects.create(
            user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'],
            verification_status=TradieProfile.VERIFICATION_APPROVED,
        )
        self.task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Kitchen sink leaking', budget=Decimal('150.00'), town='Suva',
        )

    def test_tradie_with_old_overdue_invoice_cannot_quote(self):
        from datetime import timedelta
        from django.utils import timezone
        Invoice.objects.create(
            tradie=self.tradie_user, invoice_number='INV-OVERDUE-1',
            total_amount=Decimal('50.00'), status=Invoice.STATUS_OVERDUE,
            due_date=timezone.localdate() - timedelta(days=20),
        )
        self.assertFalse(self.tradie_profile.can_quote())
        self.assertIn('overdue', self.tradie_profile.quote_block_reason())

        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.post(
            reverse('submit_quote', args=[self.task.pk]),
            {'price': '120.00', 'message': 'Can do', 'quote_includes': 'labour_only'},
            secure=True,
        )
        self.assertRedirects(response, reverse('tradie_dashboard'), fetch_redirect_response=False)
        self.assertFalse(Quote.objects.filter(task=self.task, tradie=self.tradie_user).exists())

    def test_tradie_with_recent_overdue_invoice_can_still_quote(self):
        from datetime import timedelta
        from django.utils import timezone
        Invoice.objects.create(
            tradie=self.tradie_user, invoice_number='INV-RECENT-1',
            total_amount=Decimal('50.00'), status=Invoice.STATUS_OVERDUE,
            due_date=timezone.localdate() - timedelta(days=5),
        )
        self.assertTrue(self.tradie_profile.can_quote())
        self.assertEqual(self.tradie_profile.quote_block_reason(), '')


class PasswordResetFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='resetme@example.com', password='OldPass123',
            first_name='Reset', last_name='Me', role=User.ROLE_CLIENT, town='Suva',
        )

    def _extract_reset_url(self, body):
        for line in body.splitlines():
            if 'password-reset' in line and 'http' in line:
                return line.split('here: ', 1)[-1].strip()
        return None

    def test_request_for_existing_email_sends_email_with_working_link(self):
        from django.core import mail
        response = self.client.post(reverse('password_reset_request'), {'email': 'resetme@example.com'}, secure=True)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.user.email, mail.outbox[0].to)
        reset_url = self._extract_reset_url(mail.outbox[0].body)
        self.assertIsNotNone(reset_url)

        confirm_response = self.client.post(reset_url, {'password': 'BrandNewPass456', 'password_confirm': 'BrandNewPass456'}, secure=True)
        self.assertRedirects(confirm_response, reverse('login'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass456'))
        self.assertFalse(self.user.check_password('OldPass123'))

    def test_request_for_unknown_email_gives_same_response_and_sends_nothing(self):
        from django.core import mail
        response = self.client.post(reverse('password_reset_request'), {'email': 'nobody@example.com'}, secure=True)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_token_is_rejected(self):
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        response = self.client.post(
            reverse('password_reset_confirm', args=[uidb64, 'bogus-token']),
            {'password': 'BrandNewPass456', 'password_confirm': 'BrandNewPass456'},
            secure=True,
        )
        self.assertRedirects(response, reverse('password_reset_request'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPass123'))

    def test_token_cannot_be_reused_after_password_already_changed(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        uidb64 = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)

        first = self.client.post(
            reverse('password_reset_confirm', args=[uidb64, token]),
            {'password': 'FirstNewPass789', 'password_confirm': 'FirstNewPass789'},
            secure=True,
        )
        self.assertRedirects(first, reverse('login'), fetch_redirect_response=False)

        second = self.client.post(
            reverse('password_reset_confirm', args=[uidb64, token]),
            {'password': 'SecondNewPass000', 'password_confirm': 'SecondNewPass000'},
            secure=True,
        )
        self.assertRedirects(second, reverse('password_reset_request'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('FirstNewPass789'))

    def test_mismatched_new_passwords_rejected(self):
        # Form-level, not self.client: a validation failure re-renders
        # password_reset_confirm.html, which hits the same pre-existing
        # Python 3.14 test-instrumentation crash as other rendered
        # templates in this local environment (see other tests in this
        # file for the same limitation).
        from .forms import SetNewPasswordForm
        form = SetNewPasswordForm({'password': 'OneThing123', 'password_confirm': 'AnotherThing456'})
        self.assertFalse(form.is_valid())

    def test_too_short_new_password_rejected(self):
        from .forms import SetNewPasswordForm
        form = SetNewPasswordForm({'password': 'short', 'password_confirm': 'short'})
        self.assertFalse(form.is_valid())


class TaskCategoryRequiredTests(TestCase):
    """post_task.html's category chip picker sets a plain hidden input,
    which browsers never enforce `required` on — so category was
    previously only enforced by the client-side chip click, not the
    server. TaskForm.category is now actually required=True (Django's
    ChoiceField default)."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email='catreq-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_posting_a_task_without_category_is_rejected(self):
        from .forms import TaskForm
        form = TaskForm(data={
            'title': 'Fix sink', 'category': '', 'description': 'Leaking',
            'budget': '150.00', 'town': 'Suva', 'urgency': 'this_week', 'budget_type': 'fixed',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('category', form.errors)

    def test_posting_a_task_with_category_succeeds(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.post(
            reverse('post_task'),
            {
                'title': 'Fix sink', 'category': 'plumbing', 'description': 'Leaking',
                'budget': '150.00', 'town': 'Suva', 'urgency': 'this_week', 'budget_type': 'fixed',
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Task.objects.filter(title='Fix sink', category='plumbing').exists())


class TaskDateFieldsTests(TestCase):
    """A job can have no date, a single date, or a date range. TaskForm's
    non-model date_type radio drives which of preferred_date /
    preferred_date_end actually get saved — see _clean_task_dates()."""

    def _base_data(self, **overrides):
        data = {
            'title': 'Fix sink', 'category': 'plumbing', 'description': 'Leaking',
            'budget': '150.00', 'town': 'Suva', 'urgency': 'this_week', 'budget_type': 'fixed',
        }
        data.update(overrides)
        return data

    def test_flexible_clears_any_dates(self):
        from .forms import TaskForm
        form = TaskForm(data=self._base_data(date_type='flexible', preferred_date='2026-08-01'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['preferred_date'])
        self.assertIsNone(form.cleaned_data['preferred_date_end'])

    def test_single_date_requires_a_date(self):
        from .forms import TaskForm
        form = TaskForm(data=self._base_data(date_type='single'))
        self.assertFalse(form.is_valid())

    def test_single_date_accepted(self):
        from .forms import TaskForm
        form = TaskForm(data=self._base_data(date_type='single', preferred_date='2026-08-01'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['preferred_date_end'])

    def test_range_requires_both_dates(self):
        from .forms import TaskForm
        form = TaskForm(data=self._base_data(date_type='range', preferred_date='2026-08-01'))
        self.assertFalse(form.is_valid())

    def test_range_end_before_start_rejected(self):
        from .forms import TaskForm
        form = TaskForm(data=self._base_data(
            date_type='range', preferred_date='2026-08-10', preferred_date_end='2026-08-01',
        ))
        self.assertFalse(form.is_valid())

    def test_range_accepted(self):
        from .forms import TaskForm
        form = TaskForm(data=self._base_data(
            date_type='range', preferred_date='2026-08-01', preferred_date_end='2026-08-05',
        ))
        self.assertTrue(form.is_valid(), form.errors)


class TaskDateModelDisplayTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email='datedisp-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_single_date_not_flagged_as_range(self):
        from datetime import date
        task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Leak', budget=Decimal('150.00'), town='Suva',
            preferred_date=date(2026, 8, 1),
        )
        self.assertFalse(task.is_date_range)
        self.assertEqual(task.date_display, date(2026, 8, 1))

    def test_range_is_flagged_and_displayed(self):
        from datetime import date
        task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Leak', budget=Decimal('150.00'), town='Suva',
            preferred_date=date(2026, 8, 1), preferred_date_end=date(2026, 8, 5),
        )
        self.assertTrue(task.is_date_range)
        self.assertEqual(task.date_display, '01 Aug 2026 – 05 Aug 2026')


class EditTaskDatesTests(TestCase):
    def setUp(self):
        from datetime import date
        self.client_user = User.objects.create_user(
            email='editdates-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.other_client = User.objects.create_user(
            email='editdates-other@example.com', password='pass12345',
            first_name='Other', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Leak', budget=Decimal('150.00'), town='Suva',
            preferred_date=date(2026, 8, 1),
        )

    def test_owner_can_change_a_single_date_to_a_range(self):
        from datetime import date
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.post(
            reverse('edit_task_dates', args=[self.task.pk]),
            {'date_type': 'range', 'preferred_date': '2026-08-01', 'preferred_date_end': '2026-08-05'},
            secure=True,
        )
        self.assertRedirects(response, reverse('task_detail', args=[self.task.pk]), fetch_redirect_response=False)
        self.task.refresh_from_db()
        self.assertEqual(self.task.preferred_date_end, date(2026, 8, 5))

    def test_non_owner_cannot_edit_dates(self):
        from django.http import Http404
        from django.test import RequestFactory
        from . import views
        request = RequestFactory().get(reverse('edit_task_dates', args=[self.task.pk]))
        request.user = self.other_client
        with self.assertRaises(Http404):
            views.edit_task_dates(request, self.task.pk)

    def test_dates_locked_once_task_is_completed(self):
        from datetime import date
        self.task.status = Task.STATUS_COMPLETED
        self.task.save()
        self.client.login(username=self.client_user.email, password='pass12345')
        response = self.client.post(
            reverse('edit_task_dates', args=[self.task.pk]),
            {'date_type': 'single', 'preferred_date': '2026-09-01'},
            secure=True,
        )
        self.assertRedirects(response, reverse('task_detail', args=[self.task.pk]), fetch_redirect_response=False)
        self.task.refresh_from_db()
        self.assertEqual(self.task.preferred_date, date(2026, 8, 1))


class PlatformFeeCalculationTests(TestCase):
    """calculate_platform_fee(): quantization to cents + negative-value guard."""

    def setUp(self):
        self.settings_obj = PlatformSettings.objects.create(
            success_fee_rate=Decimal('7.5'), success_fee_cap=Decimal('75.00'),
            large_job_threshold=Decimal('5000.00'), large_job_fee_rate=Decimal('3.00'), active=True,
        )

    def test_fee_is_quantized_to_cents(self):
        _, _, fee = calculate_platform_fee(Decimal('100.33'), self.settings_obj)
        self.assertEqual(fee, fee.quantize(Decimal('0.01')))
        self.assertEqual(fee, Decimal('7.52'))

    def test_negative_job_value_never_produces_a_negative_fee(self):
        _, _, fee = calculate_platform_fee(Decimal('-500.00'), self.settings_obj)
        self.assertEqual(fee, Decimal('0.00'))


class MarketFeeRateTests(TestCase):
    """Market listing pricing uses its own market_fee_rate, independent of
    the job/task success_fee_rate — the two can differ (e.g. 2% vs 7.5%)."""

    def setUp(self):
        self.settings_obj = PlatformSettings.objects.create(
            success_fee_rate=Decimal('7.5'), success_fee_cap=Decimal('75.00'),
            large_job_threshold=Decimal('5000.00'), large_job_fee_rate=Decimal('3.00'),
            market_fee_rate=Decimal('2.0'), active=True,
        )

    def test_market_take_home_uses_market_rate_not_success_rate(self):
        breakdown = calculate_market_take_home(Decimal('100.00'), 1, settings=self.settings_obj)
        self.assertEqual(breakdown['fee_rate'], Decimal('2.0'))
        self.assertEqual(breakdown['fee_amount'], Decimal('2.00'))
        self.assertEqual(breakdown['take_home_total'], Decimal('98.00'))

    def test_market_price_per_unit_uses_market_rate_not_success_rate(self):
        breakdown = calculate_market_price_per_unit(Decimal('98.00'), 1, settings=self.settings_obj)
        self.assertEqual(breakdown['fee_rate'], Decimal('2.0'))
        self.assertEqual(breakdown['total_price'], Decimal('100.00'))


class PlatformSettingsSingletonTests(TestCase):
    def test_activating_a_row_deactivates_all_others(self):
        first = PlatformSettings.objects.create(active=True)
        second = PlatformSettings.objects.create(active=False)
        second.active = True
        second.save()
        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertEqual(PlatformSettings.objects.filter(active=True).count(), 1)


class FoundingCreditAndPromoRaceGuardTests(TestCase):
    """Not true concurrency tests (that needs multiple DB connections/
    threads and is out of scope for the unit test suite) — these confirm
    the now-locked code paths still behave correctly for the ordinary,
    single-request case, i.e. the select_for_update()/F() changes didn't
    break normal behaviour."""

    def setUp(self):
        self.settings_obj = PlatformSettings.objects.create(
            success_fee_rate=Decimal('10.0'), success_fee_cap=Decimal('1000.00'),
            large_job_threshold=Decimal('5000.00'), large_job_fee_rate=Decimal('3.00'), active=True,
        )
        self.client_user = User.objects.create_user(
            email='race-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='race-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        self.profile = TradieProfile.objects.create(
            user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'],
            verification_status=TradieProfile.VERIFICATION_APPROVED,
            is_founding_member=True, founding_member_credit_balance=Decimal('50.00'),
        )
        self.task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Kitchen sink leaking', budget=Decimal('150.00'), town='Suva',
            status=Task.STATUS_COMPLETED, assigned_tradie=self.tradie_user,
            final_job_value=Decimal('200.00'), completed_at=django_timezone.now(),
        )
        self.quote = Quote.objects.create(
            task=self.task, tradie=self.tradie_user, price=Decimal('200.00'),
            message='ok', status=Quote.STATUS_ACCEPTED, used_founding_credit=True,
        )

    def test_founding_credit_is_capped_and_deducted_once(self):
        fee = create_platform_fee_for_task(self.task, Decimal('200.00'))
        self.assertEqual(fee.discount_amount, Decimal('20.00'))  # 10% of 200 = 20, balance covers it
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.founding_member_credit_balance, Decimal('30.00'))

    def test_discount_never_exceeds_the_gross_fee(self):
        self.profile.founding_member_credit_balance = Decimal('500.00')
        self.profile.save()
        fee = create_platform_fee_for_task(self.task, Decimal('200.00'))
        self.assertEqual(fee.discount_amount, fee.gross_fee_amount)
        self.assertEqual(fee.fee_amount, Decimal('0.00'))

    def test_promo_code_times_used_increments_and_respects_max_uses(self):
        promo = PromoCode.objects.create(
            code='SAVE10', discount_type=PromoCode.DISCOUNT_PERCENT, discount_value=Decimal('50'),
            active=True, max_uses=1, times_used=0,
        )
        self.quote.used_founding_credit = False
        self.quote.promo_code = promo
        self.quote.save()

        fee = create_platform_fee_for_task(self.task, Decimal('200.00'))
        promo.refresh_from_db()
        self.assertEqual(promo.times_used, 1)
        self.assertGreater(fee.discount_amount, Decimal('0.00'))

    def test_exhausted_promo_code_grants_no_discount(self):
        promo = PromoCode.objects.create(
            code='USEDUP', discount_type=PromoCode.DISCOUNT_PERCENT, discount_value=Decimal('50'),
            active=True, max_uses=1, times_used=1,
        )
        self.quote.used_founding_credit = False
        self.quote.promo_code = promo
        self.quote.save()

        fee = create_platform_fee_for_task(self.task, Decimal('200.00'))
        self.assertEqual(fee.discount_amount, Decimal('0.00'))


class DoubleInvoicingGuardTests(TestCase):
    def setUp(self):
        self.settings_obj = PlatformSettings.objects.create(active=True)
        self.client_user = User.objects.create_user(
            email='inv-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='inv-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        TradieProfile.objects.create(user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'])
        self.task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Leak', budget=Decimal('150.00'), town='Suva',
            status=Task.STATUS_COMPLETED, assigned_tradie=self.tradie_user,
            final_job_value=Decimal('150.00'), completed_at=django_timezone.now(),
        )
        self.fee = PlatformFee.objects.create(
            task=self.task, tradie=self.tradie_user, final_job_value=Decimal('150.00'),
            fee_rate=Decimal('7.5'), fee_cap=Decimal('75.00'), gross_fee_amount=Decimal('11.25'),
            fee_amount=Decimal('11.25'), status=PlatformFee.STATUS_PENDING,
        )

    def test_an_already_invoiced_fee_is_not_billed_a_second_time(self):
        from .utils import create_invoice_with_lines
        first_invoice = create_invoice_with_lines(
            tradie=self.tradie_user, period_start=django_timezone.localdate(), period_end=django_timezone.localdate(),
            fee_ids=[self.fee.pk],
        )
        self.assertEqual(first_invoice.lines.count(), 1)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.status, PlatformFee.STATUS_INVOICED)

        second_invoice = create_invoice_with_lines(
            tradie=self.tradie_user, period_start=django_timezone.localdate(), period_end=django_timezone.localdate(),
            fee_ids=[self.fee.pk],
        )
        self.assertEqual(second_invoice.lines.count(), 0)
        self.assertEqual(second_invoice.total_amount, Decimal('0.00'))


class QuoteVatTakeHomeTests(TestCase):
    """The estimated_provider_take_home saved on submit must match what the
    live JS calculator on task_detail.html shows — previously the server
    ignored VAT entirely."""

    def setUp(self):
        PlatformSettings.objects.create(
            success_fee_rate=Decimal('7.5'), success_fee_cap=Decimal('1000.00'),
            large_job_threshold=Decimal('5000.00'), large_job_fee_rate=Decimal('3.00'), active=True,
        )
        self.client_user = User.objects.create_user(
            email='vat-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='vat-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        TradieProfile.objects.create(
            user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'],
            verification_status=TradieProfile.VERIFICATION_APPROVED,
        )
        self.task = Task.objects.create(
            client=self.client_user, title='Fix sink', category='plumbing',
            description='Leak', budget=Decimal('150.00'), town='Suva',
        )

    def test_take_home_deducts_vat_after_the_platform_fee(self):
        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.post(
            reverse('submit_quote', args=[self.task.pk]),
            {
                'price': '115.00', 'message': 'Can do', 'quote_includes': 'labour_only',
                'vat_applicable': 'on', 'vat_rate': '15',
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        quote = Quote.objects.get(task=self.task, tradie=self.tradie_user)
        # fee = 7.5% of 115 = 8.625 -> quantized 8.62 (estimated_platform_fee
        # is quantized independently in submit_quote); after_fee = 115 - 8.62
        # = 106.38; take_home = 106.38 * 0.85 = 90.423 -> 90.42
        self.assertEqual(quote.estimated_platform_fee, Decimal('8.62'))
        after_fee = Decimal('115.00') - quote.estimated_platform_fee
        expected = (after_fee * Decimal('0.85')).quantize(Decimal('0.01'))
        self.assertEqual(quote.estimated_provider_take_home, expected)
        self.assertLess(quote.estimated_provider_take_home, after_fee)

    def test_no_vat_leaves_take_home_unchanged(self):
        self.client.login(username=self.tradie_user.email, password='pass12345')
        response = self.client.post(
            reverse('submit_quote', args=[self.task.pk]),
            {'price': '100.00', 'message': 'Can do', 'quote_includes': 'labour_only'},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        quote = Quote.objects.get(task=self.task, tradie=self.tradie_user)
        self.assertEqual(quote.estimated_provider_take_home, Decimal('100.00') - quote.estimated_platform_fee)


class VerificationDocumentUploadValidationTests(TestCase):
    def _base_post_data(self):
        return {
            'first_name': 'Doc', 'last_name': 'Test', 'email': 'doctest@example.com',
            'mobile': '+679 111 2222', 'town': 'Suva', 'password': 'pass12345', 'password_confirm': 'pass12345',
            'business_name': '', 'tin': '', 'years_experience': '1-3 years', 'bio': 'Bio',
            'trades': ['cleaning'], 'service_towns': ['Suva'],
            'accepted_terms': 'on', 'accepted_platform_circumvention': 'on', 'accepted_invoicing_terms': 'on',
        }

    def test_oversized_document_is_rejected(self):
        from .forms import TradieRegistrationForm
        big_file = SimpleUploadedFile('tin.pdf', b'x' * (11 * 1024 * 1024), content_type='application/pdf')
        form = TradieRegistrationForm(data=self._base_post_data(), files={'tin_letter': big_file})
        self.assertFalse(form.is_valid())
        self.assertIn('tin_letter', form.errors)

    def test_disallowed_file_type_is_rejected(self):
        from .forms import TradieRegistrationForm
        bad_file = SimpleUploadedFile('tin.exe', b'not really a pdf', content_type='application/octet-stream')
        form = TradieRegistrationForm(data=self._base_post_data(), files={'tin_letter': bad_file})
        self.assertFalse(form.is_valid())
        self.assertIn('tin_letter', form.errors)

    def test_valid_pdf_is_accepted(self):
        from .forms import TradieRegistrationForm
        good_file = SimpleUploadedFile('tin.pdf', b'%PDF-1.4 fake pdf content', content_type='application/pdf')
        form = TradieRegistrationForm(data=self._base_post_data(), files={'tin_letter': good_file})
        self.assertNotIn('tin_letter', form.errors)


class RateLimitingTests(TestCase):
    """login_view's failure/blocked paths both end in render(login.html),
    which crashes under this local environment's Python 3.14 test-
    instrumentation issue (see other tests in this file) — so the login
    tests seed the rate-limit cache key directly (rather than actually
    POSTing wrong passwords repeatedly) and call the view via
    RequestFactory with render() mocked out, inspecting cache/session
    state instead of the rendered response."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user = User.objects.create_user(
            email='ratelimit@example.com', password='CorrectPass123',
            first_name='Rate', last_name='Limit', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_login_locks_out_after_repeated_failures(self):
        from django.core.cache import cache
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory
        from unittest.mock import patch
        from . import views as marketplace_views

        cache.set(f'ratelimit:login:127.0.0.1', 10, 900)
        request = RequestFactory().post('/login/', {'email': self.user.email, 'password': 'CorrectPass123'})
        request.user = AnonymousUser()
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))
        with patch('marketplace.views.render') as mock_render:
            mock_render.return_value = None
            marketplace_views.login_view(request)
        # Blocked before authenticate() ever ran — no session key was ever
        # touched because login() was never called.
        self.assertTrue(mock_render.called)

    def test_successful_login_does_not_count_against_the_limit(self):
        # Simulate 3 prior failed attempts (below the 10-attempt threshold)
        # by seeding the cache directly, then confirm a correct-password
        # login still succeeds normally.
        from django.core.cache import cache
        cache.set('ratelimit:login:127.0.0.1', 3, 900)
        response = self.client.post(reverse('login'), {'email': self.user.email, 'password': 'CorrectPass123'}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)
        # A successful login clears the counter entirely.
        self.assertIsNone(cache.get('ratelimit:login:127.0.0.1'))

    def test_contact_support_is_rate_limited(self):
        from unittest.mock import patch
        data = {'name': 'A', 'email': 'a@example.com', 'topic': 'general', 'subject': 'Hi', 'message': 'Hello there'}
        with patch('marketplace.views.notify_admin') as mock_notify:
            for _ in range(5):
                self.client.post(reverse('contact_support'), data, secure=True)
            self.assertEqual(mock_notify.call_count, 5)
            self.client.post(reverse('contact_support'), data, secure=True)
            # 6th request is blocked before notify_admin is ever called again.
            self.assertEqual(mock_notify.call_count, 5)

    def test_password_reset_request_is_rate_limited(self):
        from django.core import mail
        for _ in range(5):
            self.client.post(reverse('password_reset_request'), {'email': self.user.email}, secure=True)
        mail.outbox.clear()
        self.client.post(reverse('password_reset_request'), {'email': self.user.email}, secure=True)
        self.assertEqual(len(mail.outbox), 0)


class PaginationTests(TestCase):
    """Rendering browse_tasks.html/browse_tradies.html/market_browse.html
    via self.client crashes under this local environment's Python 3.14
    test-instrumentation issue (see other tests in this file for the same,
    pre-existing limitation) — these call the view functions directly and
    inspect the Paginator object placed in the returned context instead of
    the rendered HTML."""

    def test_browse_tasks_paginates_at_20(self):
        from django.test import RequestFactory
        client_user = User.objects.create_user(
            email='page-client@example.com', password='pass12345',
            first_name='Page', last_name='Client', role=User.ROLE_CLIENT, town='Suva',
        )
        for i in range(25):
            Task.objects.create(
                client=client_user, title=f'Task {i}', category='cleaning',
                description='desc', budget=Decimal('50.00'), town='Suva',
            )
        from unittest.mock import patch
        request = RequestFactory().get('/tasks/')
        request.user = client_user
        with patch('marketplace.views.render') as mock_render:
            mock_render.return_value = None
            from . import views as marketplace_views
            marketplace_views.browse_tasks(request)
            ctx = mock_render.call_args[0][2]
        self.assertEqual(ctx['page_obj'].paginator.num_pages, 2)
        self.assertEqual(len(ctx['page_obj'].object_list), 20)


class CircumventionCaseAuditFieldsTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(email='super@example.com', password='pass12345')
        self.client_user = User.objects.create_user(
            email='circ-client@example.com', password='pass12345',
            first_name='Client', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='circ-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        self.case = PlatformCircumventionCase.objects.create(
            client=self.client_user, provider=self.tradie_user,
            total_job_value=Decimal('500.00'), client_fee_amount=Decimal('25.00'),
            provider_fee_amount=Decimal('25.00'),
        )

    def test_bulk_mark_paid_stamps_reviewed_by_and_reviewed_at(self):
        from .admin import PlatformCircumventionCaseAdmin
        from django.test import RequestFactory
        factory = RequestFactory()
        request = factory.post('/admin/marketplace/platformcircumventioncase/')
        request.user = self.admin_user
        # message_user requires the messages framework
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))

        model_admin = PlatformCircumventionCaseAdmin(PlatformCircumventionCase, None)
        model_admin.mark_paid(request, PlatformCircumventionCase.objects.filter(pk=self.case.pk))

        self.case.refresh_from_db()
        self.assertEqual(self.case.status, PlatformCircumventionCase.STATUS_PAID)
        self.assertEqual(self.case.reviewed_by, self.admin_user)
        self.assertIsNotNone(self.case.reviewed_at)


class ChangePasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='changepw@example.com', password='OldPass123',
            first_name='Change', last_name='PW', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_wrong_current_password_is_rejected(self):
        form = ChangePasswordForm({
            'current_password': 'WrongPass', 'new_password': 'NewPass456', 'new_password_confirm': 'NewPass456',
        }, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('current_password', form.errors)

    def test_mismatched_new_passwords_rejected(self):
        form = ChangePasswordForm({
            'current_password': 'OldPass123', 'new_password': 'NewPass456', 'new_password_confirm': 'Different789',
        }, user=self.user)
        self.assertFalse(form.is_valid())

    def test_full_change_password_flow_keeps_session_and_updates_password(self):
        self.client.login(username=self.user.email, password='OldPass123')
        response = self.client.post(
            reverse('change_password'),
            {'current_password': 'OldPass123', 'new_password': 'NewPass456', 'new_password_confirm': 'NewPass456'},
            secure=True,
        )
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)  # still logged in
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPass456'))
        self.assertFalse(self.user.check_password('OldPass123'))


class MigrateToTradieAdminValidationTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(email='migsuper@example.com', password='pass12345')
        self.client_user = User.objects.create_user(
            email='migrate-me@example.com', password='pass12345',
            first_name='Migrate', last_name='Me', role=User.ROLE_CLIENT, town='',
        )

    def test_missing_service_towns_is_rejected(self):
        # The validation-failure path re-renders migrate_to_tradie.html,
        # which crashes under this local environment's Python 3.14 test-
        # instrumentation issue (see other tests in this file) — render()
        # is mocked out so the view logic runs without hitting the
        # template engine, and DB state is asserted directly instead.
        from django.test import RequestFactory
        from unittest.mock import patch
        from . import admin as marketplace_admin

        request = RequestFactory().post(
            reverse('admin:marketplace_user_migrate_to_tradie', args=[self.client_user.pk]),
            {'trades': ['cleaning'], 'service_towns': [], 'business_name': ''},
        )
        request.user = self.admin_user
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))

        model_admin = marketplace_admin.UserAdmin(User, marketplace_admin.admin.site)
        with patch('marketplace.admin.render') as mock_render:
            mock_render.return_value = None
            model_admin.migrate_to_tradie_view(request, self.client_user.pk)
        self.assertTrue(mock_render.called)
        self.client_user.refresh_from_db()
        self.assertEqual(self.client_user.role, User.ROLE_CLIENT)
        self.assertFalse(hasattr(self.client_user, 'tradie_profile'))

    def test_valid_migration_succeeds(self):
        self.client.login(username=self.admin_user.email, password='pass12345')
        response = self.client.post(
            reverse('admin:marketplace_user_migrate_to_tradie', args=[self.client_user.pk]),
            {'trades': ['cleaning'], 'service_towns': ['Suva'], 'business_name': ''},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.client_user.refresh_from_db()
        self.assertEqual(self.client_user.role, User.ROLE_TRADIE)
        self.assertEqual(self.client_user.tradie_profile.service_towns, ['Suva'])

    def test_staff_without_permission_is_denied(self):
        limited_staff = User.objects.create_user(
            email='limited-staff@example.com', password='pass12345',
            first_name='Limited', last_name='Staff', role='', is_staff=True,
        )
        self.client.login(username=limited_staff.email, password='pass12345')
        response = self.client.post(
            reverse('admin:marketplace_user_migrate_to_tradie', args=[self.client_user.pk]),
            {'trades': ['cleaning'], 'service_towns': ['Suva'], 'business_name': ''},
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        self.client_user.refresh_from_db()
        self.assertEqual(self.client_user.role, User.ROLE_CLIENT)


class InvoiceResendGuardTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(email='invsuper@example.com', password='pass12345')
        self.tradie_user = User.objects.create_user(
            email='inv-guard-tradie@example.com', password='pass12345',
            first_name='Tradie', last_name='User', role=User.ROLE_TRADIE, town='Suva',
        )
        self.invoice = Invoice.objects.create(
            tradie=self.tradie_user, invoice_number='INV-GUARD-1', total_amount=Decimal('50.00'),
            status=Invoice.STATUS_SENT, due_date=django_timezone.localdate(),
        )

    def test_resend_without_confirmation_is_blocked(self):
        self.client.login(username=self.admin_user.email, password='pass12345')
        InvoiceNotification_count_before = self.invoice.notifications.count()
        response = self.client.post(reverse('admin:marketplace_invoice_send', args=[self.invoice.pk]), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.invoice.notifications.count(), InvoiceNotification_count_before)

    def test_resend_with_confirmation_proceeds(self):
        self.client.login(username=self.admin_user.email, password='pass12345')
        response = self.client.post(
            reverse('admin:marketplace_invoice_send', args=[self.invoice.pk]),
            {'confirm_resend': '1'}, secure=True,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.invoice.notifications.exists())

    def test_bulk_send_action_skips_already_sent_invoices(self):
        from .admin import InvoiceAdmin
        from django.test import RequestFactory
        from django.contrib.messages.storage.fallback import FallbackStorage
        factory = RequestFactory()
        request = factory.post('/admin/marketplace/invoice/')
        request.user = self.admin_user
        setattr(request, 'session', {})
        setattr(request, '_messages', FallbackStorage(request))

        model_admin = InvoiceAdmin(Invoice, None)
        model_admin.send_invoices_action(request, Invoice.objects.filter(pk=self.invoice.pk))
        self.assertFalse(self.invoice.notifications.exists())


class TermsAcceptanceReportTests(TestCase):
    """Covers the UserAdmin report added to surface accounts with no
    TermsAcceptance record — either a legacy pre-tracking account, or one
    created directly via the admin's "Add user" button, which (unlike the
    public registration forms) has no terms checkbox at all."""

    def setUp(self):
        from marketplace.models import TermsAcceptance
        self.with_terms = User.objects.create_user(
            email='hasterms@example.com', password='pass12345',
            first_name='Has', last_name='Terms', role=User.ROLE_CLIENT, town='Suva',
        )
        TermsAcceptance.objects.create(user=self.with_terms, terms_version='1.0')
        self.without_terms = User.objects.create_user(
            email='noterms@example.com', password='pass12345',
            first_name='No', last_name='Terms', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_annotation_correctly_flags_each_account(self):
        from django.contrib.admin.sites import site
        from marketplace.admin import UserAdmin
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/marketplace/user/')
        request.user = self.with_terms
        qs = UserAdmin(User, site).get_queryset(request)
        self.assertTrue(qs.get(pk=self.with_terms.pk).has_accepted_terms)
        self.assertFalse(qs.get(pk=self.without_terms.pk).has_accepted_terms)

    def test_filter_scopes_to_accounts_missing_terms(self):
        # Goes through the real admin changelist machinery (not a
        # hand-built filter instance) so the query string is parsed the
        # same way an actual admin page request would.
        from marketplace.admin import UserAdmin
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/marketplace/user/', {'accepted_terms': 'no'})
        request.user = self.with_terms
        model_admin = UserAdmin(User, site)
        changelist = model_admin.get_changelist_instance(request)
        self.assertEqual(set(changelist.get_queryset(request).values_list('pk', flat=True)), {self.without_terms.pk})


# ── Workspaces (multi-workspace accounts) ────────────────────────────────────

def _request_with_session(user):
    """RequestFactory requests have no session/auth middleware attached by
    default — build one by hand for workspaces.py functions that read/write
    request.session."""
    request = RequestFactory().get('/')
    from django.contrib.sessions.middleware import SessionMiddleware
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    return request


class WorkspaceModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='ws-model@example.com', password='pass12345',
            first_name='Ws', last_name='Model', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_one_client_workspace_per_user(self):
        Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_CLIENT, display_name='A', slug='wmt-a')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_CLIENT, display_name='B', slug='wmt-b')

    def test_one_individual_provider_workspace_per_user(self):
        Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_INDIVIDUAL_PROVIDER, display_name='A', slug='wmt-ip-a')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_INDIVIDUAL_PROVIDER, display_name='B', slug='wmt-ip-b')

    def test_multiple_business_workspaces_allowed(self):
        Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_BUSINESS, display_name='Biz A', slug='wmt-biz-a')
        Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_BUSINESS, display_name='Biz B', slug='wmt-biz-b')
        self.assertEqual(
            Workspace.objects.filter(owner=self.user, workspace_type=Workspace.TYPE_BUSINESS).count(), 2
        )

    def test_workspace_membership_unique_together(self):
        ws = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_CLIENT, display_name='A', slug='wmt-c')
        WorkspaceMembership.objects.create(workspace=ws, user=self.user, role=WorkspaceMembership.ROLE_OWNER)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkspaceMembership.objects.create(workspace=ws, user=self.user, role=WorkspaceMembership.ROLE_MANAGER)

    def test_workspace_membership_rejects_invalid_role(self):
        ws = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_CLIENT, display_name='A', slug='wmt-d')
        membership = WorkspaceMembership(workspace=ws, user=self.user, role='not-a-real-role')
        with self.assertRaises(ValidationError):
            membership.full_clean()

    def test_business_profile_rejects_non_business_workspace(self):
        ws = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_CLIENT, display_name='A', slug='wmt-e')
        profile = BusinessProfile(workspace=ws, trading_name='Test Biz')
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_business_profile_accepts_business_workspace(self):
        ws = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_BUSINESS, display_name='Biz', slug='wmt-f')
        profile = BusinessProfile(workspace=ws, trading_name='Test Biz')
        profile.full_clean()  # should not raise

    def test_save_auto_generates_slug_when_blank(self):
        # Regression: slug is editable=False (never on any ModelForm,
        # including the admin's "Add Workspace" page), so anything that
        # creates a Workspace without going through workspaces.py's
        # create_*_workspace() helpers used to insert a blank slug —
        # harmless for the first row, then a raw IntegrityError on every
        # one after it (duplicate '' slugs). Workspace.save() now generates
        # one itself whenever it's missing.
        ws = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_BUSINESS, display_name='Auto Slug Co')
        self.assertEqual(ws.slug, 'auto-slug-co')

    def test_save_slug_collision_appends_suffix_even_without_helper(self):
        other = User.objects.create_user(
            email='ws-model-2@example.com', password='p',
            first_name='Ws', last_name='Model', role=User.ROLE_CLIENT, town='Suva',
        )
        ws1 = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_BUSINESS, display_name='Same Name Ltd')
        ws2 = Workspace.objects.create(owner=other, workspace_type=Workspace.TYPE_BUSINESS, display_name='Same Name Ltd')
        self.assertEqual(ws1.slug, 'same-name-ltd')
        self.assertEqual(ws2.slug, 'same-name-ltd-2')

    def test_save_never_overwrites_an_existing_slug(self):
        ws = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_BUSINESS, display_name='Stable Slug Co')
        original_slug = ws.slug
        ws.display_name = 'Renamed Company'
        ws.save()
        ws.refresh_from_db()
        self.assertEqual(ws.slug, original_slug)

    def test_admin_add_workspace_page_works_for_a_second_colliding_display_name(self):
        # Regression test for the same bug reproduced manually against a
        # live admin session: two workspaces with the same display_name,
        # created via the actual admin "Add" view (not the ORM directly).
        admin_user = User.objects.create_superuser(email='ws-admin@example.com', password='pass12345')
        other = User.objects.create_user(
            email='ws-model-3@example.com', password='p',
            first_name='Ws', last_name='Model', role=User.ROLE_CLIENT, town='Suva',
        )
        self.client.login(username=admin_user.email, password='pass12345')
        post_data = {
            'workspace_type': Workspace.TYPE_BUSINESS, 'display_name': 'Colliding Name', 'active': 'on',
            'memberships-TOTAL_FORMS': '0', 'memberships-INITIAL_FORMS': '0',
            'memberships-MIN_NUM_FORMS': '0', 'memberships-MAX_NUM_FORMS': '1000',
        }
        r1 = self.client.post('/admin/marketplace/workspace/add/', {**post_data, 'owner': self.user.pk}, secure=True)
        self.assertEqual(r1.status_code, 302)
        r2 = self.client.post('/admin/marketplace/workspace/add/', {**post_data, 'owner': other.pk}, secure=True)
        self.assertEqual(r2.status_code, 302)
        slugs = set(Workspace.objects.filter(display_name='Colliding Name').values_list('slug', flat=True))
        self.assertEqual(slugs, {'colliding-name', 'colliding-name-2'})


class WorkspaceRaceConditionTests(TestCase):
    """_create_workspace's IntegrityError handling — see workspaces.py for
    why blindly retrying isn't safe when the failure is a genuine race on
    the owner+type uniqueness constraint rather than a slug collision."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='race@example.com', password='p',
            first_name='Race', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_create_workspace_returns_existing_row_on_owner_type_race(self):
        # Simulate the race: a workspace already exists for this owner+type
        # (as if another concurrent request just committed one), but
        # _create_workspace is called directly — bypassing
        # create_client_workspace's own pre-check — to force it down the
        # IntegrityError branch instead of the normal idempotent early-return.
        existing = Workspace.objects.create(owner=self.user, workspace_type=Workspace.TYPE_CLIENT, display_name='Existing')
        result = workspaces._create_workspace(self.user, Workspace.TYPE_CLIENT, 'Different Display Name')
        self.assertEqual(result.pk, existing.pk)
        self.assertEqual(Workspace.objects.filter(owner=self.user, workspace_type=Workspace.TYPE_CLIENT).count(), 1)


class WorkspaceOwnershipValidationTests(TestCase):
    """clean() on Task/Quote/PlatformFee/Invoice/PublicReview/PrivateReview
    rejects a workspace FK whose owner doesn't match the legacy user FK it's
    meant to mirror — this is the one place a human (via the admin) could
    introduce a mismatch by hand, since the application write paths always
    derive the workspace from the same user."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email='owncheck-client@example.com', password='p',
            first_name='Own', last_name='Client', role=User.ROLE_CLIENT, town='Suva',
        )
        self.other_client_user = User.objects.create_user(
            email='owncheck-other@example.com', password='p',
            first_name='Other', last_name='Client', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='owncheck-tradie@example.com', password='p',
            first_name='Own', last_name='Tradie', role=User.ROLE_TRADIE, town='Suva',
        )
        TradieProfile.objects.create(user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'])

        self.client_ws = workspaces.create_client_workspace(self.client_user)
        self.other_client_ws = workspaces.create_client_workspace(self.other_client_user)
        self.provider_ws = workspaces.create_individual_provider_workspace(self.tradie_user)

        self.task = Task.objects.create(
            client=self.client_user, title='Ownership check', category='cleaning',
            description='d', budget=Decimal('100'), town='Suva',
        )

    def test_task_client_workspace_mismatch_rejected(self):
        task = Task(client=self.client_user, client_workspace=self.other_client_ws)
        with self.assertRaises(ValidationError):
            task.clean()

    def test_task_client_workspace_match_accepted(self):
        Task(client=self.client_user, client_workspace=self.client_ws).clean()  # no exception

    def test_task_assigned_provider_workspace_mismatch_rejected(self):
        other_tradie = User.objects.create_user(
            email='owncheck-other-tradie@example.com', password='p',
            first_name='Other', last_name='Tradie', role=User.ROLE_TRADIE, town='Suva',
        )
        TradieProfile.objects.create(user=other_tradie, trades=['cleaning'], service_towns=['Suva'])
        other_provider_ws = workspaces.create_individual_provider_workspace(other_tradie)
        task = Task(
            client=self.client_user, assigned_tradie=self.tradie_user,
            assigned_provider_workspace=other_provider_ws,
        )
        with self.assertRaises(ValidationError):
            task.clean()

    def test_quote_provider_workspace_mismatch_rejected(self):
        quote = Quote(task=self.task, tradie=self.tradie_user, provider_workspace=self.other_client_ws)
        with self.assertRaises(ValidationError):
            quote.clean()

    def test_platform_fee_provider_workspace_mismatch_rejected(self):
        fee = PlatformFee(task=self.task, tradie=self.tradie_user, provider_workspace=self.other_client_ws)
        with self.assertRaises(ValidationError):
            fee.clean()

    def test_invoice_provider_workspace_mismatch_rejected(self):
        invoice = Invoice(tradie=self.tradie_user, provider_workspace=self.other_client_ws)
        with self.assertRaises(ValidationError):
            invoice.clean()

    def test_public_review_workspace_mismatch_rejected(self):
        review = PublicReview(
            task=self.task, rater=self.client_user, ratee=self.tradie_user,
            reviewer_workspace=self.other_client_ws, reviewed_workspace=self.provider_ws,
        )
        with self.assertRaises(ValidationError):
            review.clean()
        review = PublicReview(
            task=self.task, rater=self.client_user, ratee=self.tradie_user,
            reviewer_workspace=self.client_ws, reviewed_workspace=self.other_client_ws,
        )
        with self.assertRaises(ValidationError):
            review.clean()

    def test_private_review_workspace_mismatch_rejected(self):
        from .models import PrivateReview
        review = PrivateReview(
            task=self.task, rater=self.tradie_user, ratee=self.client_user,
            reviewer_workspace=self.other_client_ws, reviewed_workspace=self.client_ws,
        )
        with self.assertRaises(ValidationError):
            review.clean()


class WorkspaceServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='svc@example.com', password='p',
            first_name='Svc', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )

    def test_create_client_workspace_is_idempotent(self):
        ws1 = workspaces.create_client_workspace(self.user)
        ws2 = workspaces.create_client_workspace(self.user)
        self.assertEqual(ws1.pk, ws2.pk)
        self.assertEqual(
            Workspace.objects.filter(owner=self.user, workspace_type=Workspace.TYPE_CLIENT).count(), 1
        )

    def test_create_workspace_creates_owner_membership(self):
        ws = workspaces.create_client_workspace(self.user)
        self.assertTrue(
            WorkspaceMembership.objects.filter(
                workspace=ws, user=self.user, role=WorkspaceMembership.ROLE_OWNER
            ).exists()
        )

    def test_slug_collision_appends_numeric_suffix(self):
        other = User.objects.create_user(
            email='svc2@example.com', password='p',
            first_name='Svc', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        ws1 = workspaces.create_client_workspace(self.user)   # display_name 'Svc User'
        ws2 = workspaces.create_client_workspace(other)       # same display_name 'Svc User'
        self.assertNotEqual(ws1.slug, ws2.slug)
        self.assertEqual(ws2.slug, f'{ws1.slug}-2')

    def test_get_workspace_returns_none_when_absent(self):
        self.assertIsNone(workspaces.get_individual_provider_workspace(self.user))

    def test_ensure_user_capability_only_flips_can_offer_services_on(self):
        cap = workspaces.ensure_user_capability(self.user)
        self.assertTrue(cap.can_hire)
        self.assertFalse(cap.can_offer_services)
        cap = workspaces.ensure_user_capability(self.user, can_offer_services=True)
        self.assertTrue(cap.can_offer_services)
        self.assertEqual(UserCapability.objects.filter(user=self.user).count(), 1)

    def test_generate_unique_workspace_slug_preview_matches_first_real_save(self):
        preview = workspaces.generate_unique_workspace_slug('Preview Co')
        self.assertEqual(preview, 'preview-co')
        # Check the preview against the real generator's result BEFORE
        # creating the row — afterward, the preview would correctly (and
        # differently) report 'svc-user-2', since 'svc-user' is now taken.
        preview_for_user = workspaces.generate_unique_workspace_slug('Svc User')
        ws = workspaces.create_client_workspace(self.user)
        self.assertEqual(ws.slug, preview_for_user)

    def test_generate_unique_workspace_slug_preview_accounts_for_existing_rows(self):
        workspaces.create_client_workspace(self.user)  # slug: 'svc-user'
        preview = workspaces.generate_unique_workspace_slug('Svc User')
        self.assertEqual(preview, 'svc-user-2')


class ActiveWorkspaceTests(TestCase):
    def setUp(self):
        self.tradie_user = User.objects.create_user(
            email='active-tradie@example.com', password='p',
            first_name='Active', last_name='Tradie', role=User.ROLE_TRADIE, town='Suva',
        )
        TradieProfile.objects.create(user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'])
        self.client_ws = workspaces.create_client_workspace(self.tradie_user)
        self.provider_ws = workspaces.create_individual_provider_workspace(self.tradie_user)

    def test_get_accessible_workspaces_returns_owned_workspaces(self):
        accessible = workspaces.get_accessible_workspaces(self.tradie_user)
        self.assertEqual(set(accessible.values_list('id', flat=True)), {self.client_ws.id, self.provider_ws.id})

    def test_get_active_workspace_defaults_to_client_when_multiple_accessible(self):
        request = _request_with_session(self.tradie_user)
        self.assertEqual(workspaces.get_active_workspace(request), self.client_ws)

    def test_set_active_workspace_switches_and_persists_in_session(self):
        request = _request_with_session(self.tradie_user)
        result = workspaces.set_active_workspace(request, self.provider_ws.id)
        self.assertEqual(result, self.provider_ws)
        self.assertEqual(request.session[workspaces.SESSION_KEY], self.provider_ws.id)
        self.assertEqual(workspaces.get_active_workspace(request), self.provider_ws)

    def test_set_active_workspace_rejects_workspace_the_user_cannot_access(self):
        other_user = User.objects.create_user(
            email='active-other@example.com', password='p',
            first_name='Other', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        other_ws = workspaces.create_client_workspace(other_user)
        request = _request_with_session(self.tradie_user)
        self.assertIsNone(workspaces.set_active_workspace(request, other_ws.id))
        self.assertNotIn(workspaces.SESSION_KEY, request.session)

    def test_get_active_workspace_falls_back_safely_when_session_is_stale(self):
        request = _request_with_session(self.tradie_user)
        request.session[workspaces.SESSION_KEY] = 999999999  # no such workspace
        self.assertEqual(workspaces.get_active_workspace(request), self.client_ws)

    def test_single_accessible_workspace_is_auto_selected(self):
        solo_user = User.objects.create_user(
            email='active-solo@example.com', password='p',
            first_name='Solo', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        solo_ws = workspaces.create_client_workspace(solo_user)
        request = _request_with_session(solo_user)
        self.assertEqual(workspaces.get_active_workspace(request), solo_ws)
        self.assertEqual(request.session[workspaces.SESSION_KEY], solo_ws.id)


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
)
class WorkspaceWiringIntegrationTests(TestCase):
    """Confirms the real write paths (registration forms, post_task,
    submit_quote, accept_quote, complete_task, rate_tradie, rate_client)
    populate the new workspace FKs end-to-end, not just at the model level."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email='wire-client@example.com', password='pass12345',
            first_name='Wire', last_name='Client', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='wire-tradie@example.com', password='pass12345',
            first_name='Wire', last_name='Tradie', role=User.ROLE_TRADIE, town='Suva',
        )
        self.tradie_profile = TradieProfile.objects.create(
            # Not 'electrical'/'plumbing' — those are safety-critical trades
            # that also require safety_documents_reviewed=True to quote,
            # independent of verification_status (see can_quote()).
            user=self.tradie_user, trades=['carpentry'], service_towns=['Suva'],
            verification_status=TradieProfile.VERIFICATION_APPROVED,
        )
        # These two users were created directly via create_user() above (not
        # through the real registration forms), so — same as any pre-Phase-1
        # user before the 0035 backfill migration ran — they start out with
        # no workspaces. Provision them explicitly to match the real
        # post-migration/post-registration state this flow assumes.
        workspaces.create_client_workspace(self.client_user)
        workspaces.create_client_workspace(self.tradie_user)
        workspaces.create_individual_provider_workspace(self.tradie_user)

    def test_client_registration_provisions_capability_and_workspace(self):
        response = self.client.post(
            reverse('register_client'),
            {
                'first_name': 'New', 'last_name': 'Reg', 'email': 'new-reg-client@example.com',
                'mobile': '+679 123 4567', 'town': 'Suva',
                'password': 'pass12345', 'password_confirm': 'pass12345', 'accepted_terms': 'on',
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='new-reg-client@example.com')
        self.assertTrue(UserCapability.objects.filter(user=user, can_hire=True).exists())
        self.assertTrue(Workspace.objects.filter(owner=user, workspace_type=Workspace.TYPE_CLIENT).exists())

    def test_tradie_registration_provisions_both_workspaces(self):
        response = self.client.post(
            reverse('register_tradie'),
            {
                'first_name': 'New', 'last_name': 'Tradie', 'email': 'new-reg-tradie@example.com',
                'mobile': '+679 111 2222', 'town': 'Suva',
                'password': 'pass12345', 'password_confirm': 'pass12345',
                'business_name': '', 'tin': '', 'years_experience': '1-3 years',
                'bio': 'Bio', 'trades': ['cleaning'], 'service_towns': ['Suva'],
                'accepted_terms': 'on', 'accepted_platform_circumvention': 'on', 'accepted_invoicing_terms': 'on',
                'tin_letter': SimpleUploadedFile('tin.pdf', b'pdf-content', content_type='application/pdf'),
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='new-reg-tradie@example.com')
        capability = UserCapability.objects.get(user=user)
        self.assertTrue(capability.can_hire)
        self.assertTrue(capability.can_offer_services)
        self.assertTrue(Workspace.objects.filter(owner=user, workspace_type=Workspace.TYPE_CLIENT).exists())
        self.assertTrue(Workspace.objects.filter(owner=user, workspace_type=Workspace.TYPE_INDIVIDUAL_PROVIDER).exists())

    def test_full_task_lifecycle_populates_workspace_fks_at_every_step(self):
        self.client.login(username=self.client_user.email, password='pass12345')
        post_response = self.client.post(
            reverse('post_task'),
            {
                'title': 'Rewire lounge', 'category': 'electrical', 'description': 'Rewire the lounge',
                'budget': '500.00', 'town': 'Suva', 'urgency': 'this_week', 'budget_type': 'fixed',
            },
            secure=True,
        )
        self.assertEqual(post_response.status_code, 302)
        task = Task.objects.get(title='Rewire lounge')
        client_ws = workspaces.get_client_workspace(self.client_user)
        self.assertIsNotNone(client_ws)
        self.assertEqual(task.client_workspace, client_ws)

        self.client.logout()
        self.client.login(username=self.tradie_user.email, password='pass12345')
        quote_response = self.client.post(
            reverse('submit_quote', args=[task.pk]),
            {'price': '480.00', 'message': 'Can start Monday', 'quote_includes': 'labour_only'},
            secure=True,
        )
        self.assertEqual(quote_response.status_code, 302)
        quote = Quote.objects.get(task=task, tradie=self.tradie_user)
        provider_ws = workspaces.get_individual_provider_workspace(self.tradie_user)
        self.assertIsNotNone(provider_ws)
        self.assertEqual(quote.provider_workspace, provider_ws)

        self.client.logout()
        self.client.login(username=self.client_user.email, password='pass12345')
        accept_response = self.client.post(reverse('accept_quote', args=[task.pk, quote.pk]), secure=True)
        self.assertEqual(accept_response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.assigned_provider_workspace, provider_ws)

        complete_response = self.client.post(reverse('complete_task', args=[task.pk]), secure=True)
        self.assertEqual(complete_response.status_code, 302)
        fee = PlatformFee.objects.get(task=task)
        self.assertEqual(fee.provider_workspace, provider_ws)

        rate_tradie_response = self.client.post(
            reverse('rate_tradie', args=[task.pk]),
            {
                'reliability_punctuality': 5, 'quote_price_accuracy': 5, 'value_for_money': 5,
                'service_quality_workmanship': 5, 'communication_after_service': 5, 'timeline_schedule_delivery': 5,
                'comment': 'Great work',
            },
            secure=True,
        )
        self.assertEqual(rate_tradie_response.status_code, 302)
        public_review = PublicReview.objects.get(task=task)
        self.assertEqual(public_review.reviewer_workspace, client_ws)
        self.assertEqual(public_review.reviewed_workspace, provider_ws)

        self.client.logout()
        self.client.login(username=self.tradie_user.email, password='pass12345')
        rate_client_response = self.client.post(
            reverse('rate_client', args=[task.pk]),
            {
                'access_readiness': 5, 'scope_clarity': 5, 'communication': 5, 'payment': 5, 'conduct': 5,
                'comment': 'Great client',
            },
            secure=True,
        )
        self.assertEqual(rate_client_response.status_code, 302)
        from .models import PrivateReview
        private_review = PrivateReview.objects.get(task=task)
        self.assertEqual(private_review.reviewer_workspace, provider_ws)
        self.assertEqual(private_review.reviewed_workspace, client_ws)

    def test_admin_migrate_to_tradie_provisions_individual_provider_workspace(self):
        admin_user = User.objects.create_superuser(email='wire-super@example.com', password='pass12345')
        client_only_user = User.objects.create_user(
            email='wire-migrate@example.com', password='pass12345',
            first_name='Migrate', last_name='Me', role=User.ROLE_CLIENT, town='Suva',
        )
        self.client.login(username=admin_user.email, password='pass12345')
        response = self.client.post(
            reverse('admin:marketplace_user_migrate_to_tradie', args=[client_only_user.pk]),
            {'trades': ['cleaning'], 'service_towns': ['Suva'], 'business_name': ''},
            secure=True,
        )
        self.assertEqual(response.status_code, 302)
        capability = UserCapability.objects.get(user=client_only_user)
        self.assertTrue(capability.can_offer_services)
        self.assertTrue(
            Workspace.objects.filter(owner=client_only_user, workspace_type=Workspace.TYPE_INDIVIDUAL_PROVIDER).exists()
        )


class AuditWorkspaceRelationshipsCommandTests(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email='audit-client@example.com', password='p',
            first_name='Audit', last_name='Client', role=User.ROLE_CLIENT, town='Suva',
        )
        self.tradie_user = User.objects.create_user(
            email='audit-tradie@example.com', password='p',
            first_name='Audit', last_name='Tradie', role=User.ROLE_TRADIE, town='Suva',
        )
        TradieProfile.objects.create(user=self.tradie_user, trades=['cleaning'], service_towns=['Suva'])
        self.client_ws = workspaces.create_client_workspace(self.client_user)
        self.provider_ws = workspaces.create_individual_provider_workspace(self.tradie_user)
        # Created via .objects.create() directly (bypassing post_task), so
        # client_workspace starts out null — exactly the "missing_repairable" case.
        self.task = Task.objects.create(
            client=self.client_user, title='Audit task', category='cleaning',
            description='d', budget=Decimal('100'), town='Suva',
        )

    def _run(self, repair=False):
        out = StringIO()
        call_command('audit_workspace_relationships', repair=repair, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_without_modifying_data(self):
        output = self._run(repair=False)
        self.assertIn('missing_repairable=1', output)
        self.task.refresh_from_db()
        self.assertIsNone(self.task.client_workspace)

    def test_repair_fills_resolvable_missing_fk(self):
        output = self._run(repair=True)
        self.assertIn('missing_repairable=1', output)
        self.task.refresh_from_db()
        self.assertEqual(self.task.client_workspace, self.client_ws)

    def test_repair_never_touches_a_mismatched_fk(self):
        other_user = User.objects.create_user(
            email='audit-other@example.com', password='p',
            first_name='Other', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        other_ws = workspaces.create_client_workspace(other_user)
        # Bypass clean()/the write paths to simulate a hand-introduced mismatch.
        Task.objects.filter(pk=self.task.pk).update(client_workspace=other_ws)

        output = self._run(repair=True)
        self.assertIn('mismatched=1', output)
        self.task.refresh_from_db()
        self.assertEqual(self.task.client_workspace_id, other_ws.id)


@override_settings(
    FACEBOOK_LOGIN_ENABLED=True,
    META_APP_ID='test-app-id',
    META_APP_SECRET='test-app-secret',
    META_OAUTH_REDIRECT_URI='https://testserver/integrations/facebook/callback/',
)
class FacebookLoginTests(TestCase):
    """meta_client is mocked throughout — no real Graph API calls. See
    marketplace/meta_client.py's own docstring for why: every real HTTP call
    goes through a named function there specifically so tests can mock it
    directly instead of needing an HTTP-mocking library."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_disabled_flag_404s(self):
        with override_settings(FACEBOOK_LOGIN_ENABLED=False):
            response = self.client.get(reverse('facebook_connect'), secure=True)
        self.assertEqual(response.status_code, 404)

    def test_connect_redirects_to_facebook_and_stores_state(self):
        response = self.client.get(reverse('facebook_connect'), secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('https://www.facebook.com/'))
        self.assertIn('facebook_oauth_state', self.client.session)

    def test_callback_missing_state_is_rejected(self):
        response = self.client.get(reverse('facebook_callback'), {'code': 'abc'}, secure=True)
        self.assertRedirects(response, reverse('login'))
        self.assertFalse(User.objects.filter(email__startswith='facebook-').exists())

    def test_callback_state_mismatch_is_rejected(self):
        session = self.client.session
        session['facebook_oauth_state'] = 'expected-state'
        session.save()
        response = self.client.get(
            reverse('facebook_callback'), {'code': 'abc', 'state': 'wrong-state'}, secure=True,
        )
        self.assertRedirects(response, reverse('login'))

    def _do_callback(self, code='auth-code', state='matching-state'):
        session = self.client.session
        session['facebook_oauth_state'] = state
        session.save()
        return self.client.get(
            reverse('facebook_callback'), {'code': code, 'state': state}, secure=True,
        )

    @mock.patch('marketplace.meta_client.get_user_profile')
    @mock.patch('marketplace.meta_client.exchange_code_for_token')
    def test_callback_creates_new_user_and_client_workspace(self, mock_exchange, mock_profile):
        mock_exchange.return_value = {'access_token': 'user-token'}
        mock_profile.return_value = {'id': 'fb-12345', 'name': 'Josaia Vuli', 'email': 'josaia@example.com'}

        response = self._do_callback()
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

        user = User.objects.get(email='josaia@example.com')
        self.assertEqual(user.first_name, 'Josaia')
        self.assertEqual(user.last_name, 'Vuli')
        self.assertEqual(user.role, User.ROLE_CLIENT)
        self.assertFalse(user.has_usable_password())

        identity = SocialIdentity.objects.get(provider_user_id='fb-12345')
        self.assertEqual(identity.user, user)
        self.assertEqual(identity.provider, SocialIdentity.PROVIDER_FACEBOOK)

        self.assertIsNotNone(workspaces.get_client_workspace(user))
        self.assertIn('_auth_user_id', self.client.session)

    @mock.patch('marketplace.meta_client.get_user_profile')
    @mock.patch('marketplace.meta_client.exchange_code_for_token')
    def test_callback_logs_in_existing_identity(self, mock_exchange, mock_profile):
        user = User.objects.create_user(
            email='existing@example.com', password=None,
            first_name='Existing', last_name='User', role=User.ROLE_CLIENT, town='Suva',
        )
        SocialIdentity.objects.create(
            user=user, provider=SocialIdentity.PROVIDER_FACEBOOK, provider_user_id='fb-99999',
        )
        mock_exchange.return_value = {'access_token': 'user-token'}
        mock_profile.return_value = {'id': 'fb-99999', 'name': 'Existing User', 'email': 'existing@example.com'}

        response = self._do_callback()
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        # No duplicate identity or account created.
        self.assertEqual(SocialIdentity.objects.filter(provider_user_id='fb-99999').count(), 1)
        self.assertEqual(User.objects.filter(email='existing@example.com').count(), 1)

    @mock.patch('marketplace.meta_client.get_user_profile')
    @mock.patch('marketplace.meta_client.exchange_code_for_token')
    def test_callback_email_collision_is_a_challenge_not_a_silent_merge(self, mock_exchange, mock_profile):
        """An email match alone must never silently attach a Facebook
        identity to an existing account — the account holder has to
        authenticate with their password first."""
        existing = User.objects.create_user(
            email='shared@example.com', password='RealPassword123',
            first_name='Real', last_name='Owner', role=User.ROLE_CLIENT, town='Suva',
        )
        mock_exchange.return_value = {'access_token': 'user-token'}
        mock_profile.return_value = {'id': 'fb-55555', 'name': 'Impersonator', 'email': 'shared@example.com'}

        response = self._do_callback()
        self.assertRedirects(response, reverse('login'))
        self.assertFalse(SocialIdentity.objects.filter(provider_user_id='fb-55555').exists())
        self.assertNotIn('_auth_user_id', self.client.session)
        existing.refresh_from_db()  # untouched

    @mock.patch('marketplace.meta_client.get_user_profile')
    @mock.patch('marketplace.meta_client.exchange_code_for_token')
    def test_callback_links_facebook_to_the_currently_logged_in_user(self, mock_exchange, mock_profile):
        user = User.objects.create_user(
            email='linkme@example.com', password='RealPassword123',
            first_name='Link', last_name='Me', role=User.ROLE_CLIENT, town='Suva',
        )
        self.client.login(username='linkme@example.com', password='RealPassword123')
        mock_exchange.return_value = {'access_token': 'user-token'}
        mock_profile.return_value = {'id': 'fb-77777', 'name': 'Link Me', 'email': ''}

        response = self._do_callback()
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        identity = SocialIdentity.objects.get(provider_user_id='fb-77777')
        self.assertEqual(identity.user, user)

    @mock.patch('marketplace.meta_client.exchange_code_for_token')
    def test_callback_graph_api_error_shows_friendly_message_not_crash(self, mock_exchange):
        mock_exchange.side_effect = meta_client.GraphAPIError('boom')
        response = self._do_callback()
        self.assertRedirects(response, reverse('login'))

    def test_callback_is_rate_limited(self):
        from django.core.cache import cache
        cache.set('ratelimit:facebook_callback:127.0.0.1', 20, 300)
        response = self._do_callback()
        self.assertRedirects(response, reverse('login'))


@override_settings(META_APP_SECRET='test-app-secret')
class MetaDataDeletionTests(TestCase):
    def _signed_request(self, payload, secret='test-app-secret'):
        import base64
        import hashlib
        import hmac
        import json as _json

        def b64(data):
            return base64.urlsafe_b64encode(data).rstrip(b'=')

        encoded_payload = b64(_json.dumps(payload).encode())
        sig = hmac.new(secret.encode(), encoded_payload, hashlib.sha256).digest()
        return (b64(sig) + b'.' + encoded_payload).decode()

    def test_valid_request_with_existing_identity_deletes_it(self):
        user = User.objects.create_user(
            email='deleteme@example.com', password=None,
            first_name='Delete', last_name='Me', role=User.ROLE_CLIENT, town='Suva',
        )
        SocialIdentity.objects.create(
            user=user, provider=SocialIdentity.PROVIDER_FACEBOOK, provider_user_id='fb-delete-1',
        )
        signed = self._signed_request({'user_id': 'fb-delete-1', 'algorithm': 'HMAC-SHA256'})

        response = self.client.post(reverse('meta_data_deletion'), {'signed_request': signed}, secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('confirmation_code', body)
        self.assertFalse(SocialIdentity.objects.filter(provider_user_id='fb-delete-1').exists())

        deletion_request = SocialDataDeletionRequest.objects.get(confirmation_code=body['confirmation_code'])
        self.assertEqual(deletion_request.status, SocialDataDeletionRequest.STATUS_COMPLETED)
        self.assertEqual(deletion_request.user, user)

    def test_no_matching_identity_still_returns_a_confirmation(self):
        signed = self._signed_request({'user_id': 'fb-unknown', 'algorithm': 'HMAC-SHA256'})
        response = self.client.post(reverse('meta_data_deletion'), {'signed_request': signed}, secure=True)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        deletion_request = SocialDataDeletionRequest.objects.get(confirmation_code=body['confirmation_code'])
        self.assertEqual(deletion_request.status, SocialDataDeletionRequest.STATUS_NOT_FOUND)
        self.assertIsNone(deletion_request.user)

    def test_bad_signature_is_rejected(self):
        signed = self._signed_request({'user_id': 'fb-x', 'algorithm': 'HMAC-SHA256'}, secret='wrong-secret')
        response = self.client.post(reverse('meta_data_deletion'), {'signed_request': signed}, secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialDataDeletionRequest.objects.exists())

    def test_status_endpoint_reports_back_the_recorded_status(self):
        signed = self._signed_request({'user_id': 'fb-status', 'algorithm': 'HMAC-SHA256'})
        create_response = self.client.post(reverse('meta_data_deletion'), {'signed_request': signed}, secure=True)
        code = create_response.json()['confirmation_code']

        status_response = self.client.get(reverse('meta_data_deletion_status', args=[code]), secure=True)
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()['status'], SocialDataDeletionRequest.STATUS_NOT_FOUND)

    def test_unknown_confirmation_code_404s(self):
        response = self.client.get(reverse('meta_data_deletion_status', args=['does-not-exist']), secure=True)
        self.assertEqual(response.status_code, 404)
