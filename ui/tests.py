import json
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from . import auth_backends, email_queue, views
from .models import LoginFailure, QueuedEmail, UserProfile


def make_questions(rw=54, math=44):
    questions = []
    for i in range(rw + math):
        questions.append({
            'id': i + 1,
            'type': 'rw' if i < rw else 'math',
            'question': f'Question {i + 1}?',
            'options': ['a', 'b', 'c', 'd'],
        })
    return questions


class QuestionsFileTestCase(TestCase):
    """Points views.QUESTIONS_FILE at a temp copy so tests never touch the real file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)
        self.path = os.path.join(self.tmpdir, 'questions.json')
        self.write_file(make_questions())
        patcher = mock.patch.object(views, 'QUESTIONS_FILE', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        views._modules_cache = None

    def write_file(self, data):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(data, f)


class ValidateQuestionsTests(TestCase):
    def test_valid_bank_has_no_errors_or_warnings(self):
        self.assertEqual(views.validate_questions(make_questions()), ([], []))

    def test_rejects_non_list(self):
        errors, _ = views.validate_questions({'id': 1})
        self.assertTrue(errors)

    def test_rejects_bad_entries(self):
        data = make_questions()
        data[0]['type'] = 'science'
        data[1]['id'] = data[2]['id']
        data[3]['question'] = '   '
        data[4]['options'] = 'abcd'
        data[5]['id'] = '6'
        errors, _ = views.validate_questions(data)
        self.assertEqual(len(errors), 5)

    def test_short_bank_is_a_warning_not_an_error(self):
        errors, warnings = views.validate_questions(make_questions(rw=53))
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)


class ApiQuestionsTests(QuestionsFileTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser('admin@example.com', 'admin@example.com', 'pw')
        self.client.force_login(self.admin)

    def post(self, data):
        return self.client.post('/api/questions/', json.dumps(data), content_type='application/json')

    def test_invalid_payload_leaves_file_untouched(self):
        with open(self.path, 'rb') as f:
            before = f.read()
        response = self.post([{'id': 1}])
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()['details'])
        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), before)

    def test_malformed_json_is_rejected(self):
        response = self.client.post('/api/questions/', '{not json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_valid_save_writes_file_and_returns_warnings(self):
        data = make_questions(math=43)
        response = self.post(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['warnings']), 1)
        with open(self.path, encoding='utf-8') as f:
            self.assertEqual(json.load(f), data)
        self.assertEqual(
            [n for n in os.listdir(self.tmpdir) if n != 'questions.json'], [],
            "temp file left behind",
        )

    def test_failed_write_keeps_old_file(self):
        with open(self.path, 'rb') as f:
            before = f.read()
        with mock.patch.object(views.os, 'replace', side_effect=OSError('disk full')):
            response = self.post(make_questions())
        self.assertEqual(response.status_code, 500)
        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(os.listdir(self.tmpdir), ['questions.json'])

    def test_non_superuser_forbidden(self):
        user = User.objects.create_user('u@example.com', 'u@example.com', 'pw')
        self.client.force_login(user)
        self.assertEqual(self.post(make_questions()).status_code, 403)


class ModulesCacheTests(QuestionsFileTestCase):
    def test_modules_split_by_type(self):
        modules = json.loads(views.get_cached_modules_json())
        self.assertEqual([m['count'] for m in modules], [27, 27, 22, 22])
        self.assertEqual({q['type'] for q in modules[2]['questions']}, {'math'})

    def test_reloads_when_file_changes(self):
        views.get_cached_modules_json()
        data = make_questions()
        data[0]['question'] = 'Changed?'
        self.write_file(data)
        # Make sure the mtime differs even on coarse-grained filesystems
        stat = os.stat(self.path)
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        modules = json.loads(views.get_cached_modules_json())
        self.assertEqual(modules[0]['questions'][0]['question'], 'Changed?')

    def test_short_module_count_matches_its_questions(self):
        self.write_file(make_questions(rw=40))
        modules = json.loads(views.get_cached_modules_json())
        self.assertEqual(modules[1]['count'], 13)
        self.assertEqual(len(modules[1]['questions']), 13)

    def test_test_page_returns_503_when_file_is_broken(self):
        user = User.objects.create_user('u@example.com', 'u@example.com', 'pw')
        self.client.force_login(user)
        with open(self.path, 'w') as f:
            f.write('{broken')
        with self.assertLogs('ui.views', 'ERROR'):
            response = self.client.get('/test/')
        self.assertEqual(response.status_code, 503)

    def test_failed_load_is_not_cached(self):
        with open(self.path, 'w') as f:
            f.write('{broken')
        with self.assertRaises(ValueError):
            views.get_cached_modules_json()
        self.write_file(make_questions())
        self.assertEqual(len(json.loads(views.get_cached_modules_json())), 4)


class EmailQueueTests(TestCase):
    def enqueue(self):
        email_queue.enqueue_email(
            subject='Code', message='Body', from_email='from@example.com',
            recipient_list=['to@example.com'],
        )
        return QueuedEmail.objects.latest('id')

    def test_enqueue_stores_and_process_one_sends(self):
        email = self.enqueue()
        self.assertEqual(email.status, QueuedEmail.PENDING)
        self.assertTrue(email_queue.process_one())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['to@example.com'])
        email.refresh_from_db()
        self.assertEqual(email.status, QueuedEmail.SENT)
        self.assertFalse(email_queue.process_one())

    def test_failure_schedules_retry_then_gives_up(self):
        email = self.enqueue()
        with mock.patch.object(email_queue.EmailMultiAlternatives, 'send', side_effect=RuntimeError('boom')):
            for attempt in range(1, email_queue._MAX_ATTEMPTS + 1):
                self.assertTrue(email_queue.process_one())
                email.refresh_from_db()
                self.assertEqual(email.attempts, attempt)
                if email.status == QueuedEmail.PENDING:
                    self.assertGreater(email.next_attempt_at, timezone.now())
                    # Not due yet, so nothing to send
                    self.assertFalse(email_queue.process_one())
                    QueuedEmail.objects.filter(id=email.id).update(next_attempt_at=timezone.now())
        self.assertEqual(email.status, QueuedEmail.FAILED)
        self.assertIn('boom', email.last_error)

    def test_email_claimed_by_another_worker_is_skipped(self):
        email = self.enqueue()
        QueuedEmail.objects.filter(id=email.id).update(
            status=QueuedEmail.SENDING, claimed_at=timezone.now())
        self.assertFalse(email_queue.process_one())

    def test_stale_claim_is_retried(self):
        email = self.enqueue()
        QueuedEmail.objects.filter(id=email.id).update(
            status=QueuedEmail.SENDING, claimed_at=timezone.now() - timedelta(minutes=10))
        self.assertTrue(email_queue.process_one())
        email.refresh_from_db()
        self.assertEqual(email.status, QueuedEmail.SENT)

    @override_settings(EMAIL_SENDER_PROCESSES=9)
    def test_rate_budget_is_split_across_processes(self):
        self.assertLessEqual(email_queue._per_process_rps() * settings.EMAIL_SENDER_PROCESSES,
                             email_queue._MAX_RPS_TOTAL)


class LoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'right-pw')

    def login(self, email='student@example.com', password='right-pw'):
        return self.client.post('/cb_login/', {'email': email, 'password': password})

    def test_success_redirects_to_dashboard(self):
        self.assertRedirects(self.login(), '/dashboard/')

    def test_wrong_password_shows_error_and_keeps_email(self):
        response = self.login(password='wrong')
        self.assertContains(response, 'Your email or password is wrong. Please try again.')
        self.assertContains(response, 'value="student@example.com"')

    def test_empty_fields_show_error(self):
        self.assertContains(self.login(password=''), 'Enter your email and password.')

    def test_account_locks_after_too_many_failures(self):
        for _ in range(auth_backends.LOGIN_MAX_FAILURES):
            self.login(password='wrong')
        # Even the right password is refused while locked
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Too many sign-in attempts', status_code=429)

        # Case changes don't get around the lock
        self.assertEqual(self.login(email='STUDENT@example.com').status_code, 429)

    def test_lock_expires_after_window(self):
        for _ in range(auth_backends.LOGIN_MAX_FAILURES):
            self.login(password='wrong')
        LoginFailure.objects.update(
            created_at=timezone.now() - auth_backends.LOGIN_FAILURE_WINDOW - timedelta(seconds=1))
        self.assertRedirects(self.login(), '/dashboard/')

    def test_success_clears_failures(self):
        self.login(password='wrong')
        self.login()
        self.assertFalse(LoginFailure.objects.exists())

    def test_lock_is_per_account(self):
        User.objects.create_user('other@example.com', 'other@example.com', 'other-pw')
        for _ in range(auth_backends.LOGIN_MAX_FAILURES):
            self.login(password='wrong')
        self.assertRedirects(self.login(email='other@example.com', password='other-pw'), '/dashboard/')


class UpdateProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        self.client.force_login(self.user)

    def test_valid_profile_saves(self):
        self.client.post('/update-profile/', {
            'full_name': 'Ana Lee', 'contact_email': 'ana@example.com', 'test_center_address': 'Here'})
        self.assertEqual(UserProfile.objects.get(user=self.user).contact_email, 'ana@example.com')

    def test_invalid_profile_shows_error_on_dashboard(self):
        response = self.client.post('/update-profile/', {
            'full_name': 'Ana Lee', 'contact_email': 'not-an-email', 'test_center_address': 'Here'},
            follow=True)
        self.assertContains(response, 'Profile not saved.')
        self.assertContains(response, 'Contact email')
        self.assertIsNone(UserProfile.objects.get(user=self.user).contact_email)


class AdminLockoutTests(TestCase):
    def test_admin_login_is_locked_too(self):
        User.objects.create_superuser('boss@example.com', 'boss@example.com', 'right-pw')
        for _ in range(auth_backends.LOGIN_MAX_FAILURES):
            self.client.post('/admin/login/', {'username': 'boss@example.com', 'password': 'wrong'})
        self.client.post('/admin/login/', {'username': 'boss@example.com', 'password': 'right-pw'})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_lock_is_shared_with_student_sign_in(self):
        User.objects.create_user('student@example.com', 'student@example.com', 'right-pw')
        for _ in range(auth_backends.LOGIN_MAX_FAILURES):
            self.client.post('/admin/login/', {'username': 'student@example.com', 'password': 'wrong'})
        response = self.client.post('/cb_login/', {'email': 'student@example.com', 'password': 'right-pw'})
        self.assertEqual(response.status_code, 429)


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        self.client.force_login(self.user)

    def test_post_logs_out(self):
        self.assertRedirects(self.client.post('/logout/'), '/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertRedirects(self.client.get('/dashboard/'), '/?next=/dashboard/', fetch_redirect_response=False)

    def test_get_does_not_log_out(self):
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)

    def test_dashboard_sign_out_posts_to_logout(self):
        UserProfile.objects.create(user=self.user, test_completed=True)
        self.assertContains(self.client.get('/dashboard/'), 'action="/logout/"')


class StartCodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        UserProfile.objects.create(user=self.user, contact_email='student@example.com')
        self.client.force_login(self.user)

    def post(self, code):
        return self.client.post('/start-code/', {'full_code': code})

    def test_valid_code_queues_email(self):
        self.assertRedirects(self.post('123456'), '/test/', fetch_redirect_response=False)
        email = QueuedEmail.objects.get()
        self.assertEqual(email.user, self.user)
        self.assertIn('123456', email.subject)

    def test_email_content_sender_and_tags(self):
        self.post('123456')
        self.assertTrue(email_queue.process_one())
        sent = mail.outbox[0]
        self.assertEqual(sent.from_email, 'BB Test <messages@bbtest.shop>')
        self.assertEqual(sent.subject, 'Your practice test start code: 123456')
        self.assertEqual(sent.to, ['student@example.com'])
        self.assertIn('Your start code is 123456', sent.body)
        html, mimetype = sent.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn('123456', html)
        for text in (sent.from_email, sent.subject, sent.body, html):
            self.assertNotIn('Bluebook', text)
        # Resend tags still identify the student
        self.assertEqual(sent.esp_extra['tags'], [
            {'name': 'user_id', 'value': str(self.user.pk)},
            {'name': 'username', 'value': 'student_example_com'},
        ])

    def test_invalid_codes_are_rejected(self):
        for code in ['12345', '1234567', '123456\n', 'abcdef', '12345\n', 'Click here http://evil.example', '']:
            response = self.post(code)
            self.assertEqual(response.status_code, 200, code)
            self.assertContains(response, 'display: block')
        self.assertFalse(QueuedEmail.objects.exists())
        self.assertIsNone(UserProfile.objects.get(user=self.user).start_code)

    def test_emails_capped_per_hour(self):
        for _ in range(views.START_CODE_EMAILS_PER_HOUR + 3):
            self.assertRedirects(self.post('123456'), '/test/', fetch_redirect_response=False)
        self.assertEqual(QueuedEmail.objects.count(), views.START_CODE_EMAILS_PER_HOUR)

        # Older emails no longer count
        QueuedEmail.objects.update(created_at=timezone.now() - timedelta(hours=2))
        self.post('123456')
        self.assertEqual(QueuedEmail.objects.count(), views.START_CODE_EMAILS_PER_HOUR + 1)


class PasswordHasherTests(TestCase):
    def test_new_passwords_use_200k_iterations(self):
        user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        algorithm, iterations, _, _ = user.password.split('$')
        self.assertEqual((algorithm, iterations), ('pbkdf2_sha256', '200000'))

    def test_old_1m_hash_still_works_and_is_downgraded_once(self):
        from django.contrib.auth.hashers import PBKDF2PasswordHasher
        user = User.objects.create_user('student@example.com', 'student@example.com', 'x')
        old_hash = PBKDF2PasswordHasher().encode('right-pw', 'somesalt123456')
        self.assertIn('$1000000$', old_hash)
        User.objects.filter(pk=user.pk).update(password=old_hash)

        response = self.client.post('/cb_login/', {'email': 'student@example.com', 'password': 'right-pw'})
        self.assertRedirects(response, '/dashboard/')
        user.refresh_from_db()
        self.assertIn('$200000$', user.password)

        # A second sign-in doesn't re-hash again
        rehashed = user.password
        self.client.logout()
        self.client.post('/cb_login/', {'email': 'student@example.com', 'password': 'right-pw'})
        user.refresh_from_db()
        self.assertEqual(user.password, rehashed)
