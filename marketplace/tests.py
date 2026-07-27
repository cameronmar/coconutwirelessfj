import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import ServiceAreaForm
from .models import Invoice, InvoiceLine, Quote, Task, TradieProfile, User
from .utils import send_invoice_notifications


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
