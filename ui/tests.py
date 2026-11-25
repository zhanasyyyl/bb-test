import io
import json
import os
import shutil
import tempfile
import time
import urllib.error
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import auth_backends, message_queue, telegram, views
from .models import LoginFailure, QueuedMessage, SiteSettings, UserProfile


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


class SkipSettingTests(QuestionsFileTestCase):
    STUDENT_ALLOWED = 'const canSkip = true;'
    NOT_ALLOWED = 'const canSkip = false;'

    def setUp(self):
        super().setUp()
        self.student = User.objects.create_user('student@example.com', password='pw')
        self.staff = User.objects.create_user('staff@example.com', password='pw', is_staff=True)

    def page_for(self, user):
        self.client.force_login(user)
        return self.client.get('/test/').content.decode()

    def run_command(self, *args):
        out = io.StringIO()
        call_command('skip_test', *args, stdout=out)
        return out.getvalue()

    def test_off_by_default_for_students(self):
        self.assertIn(self.NOT_ALLOWED, self.page_for(self.student))

    def test_reading_the_setting_does_not_create_the_row(self):
        self.page_for(self.student)
        self.assertFalse(SiteSettings.objects.exists())

    def test_staff_can_always_skip(self):
        self.assertIn(self.STUDENT_ALLOWED, self.page_for(self.staff))
        self.run_command('off')
        self.assertIn(self.STUDENT_ALLOWED, self.page_for(self.staff))

    def test_superuser_who_is_not_staff_follows_the_setting(self):
        admin = User.objects.create_user('root@example.com', password='pw', is_superuser=True)
        self.assertIn(self.NOT_ALLOWED, self.page_for(admin))

    def test_on_then_off_for_students(self):
        self.run_command('on')
        self.assertIn(self.STUDENT_ALLOWED, self.page_for(self.student))
        self.run_command('off')
        self.assertIn(self.NOT_ALLOWED, self.page_for(self.student))

    def test_repeating_a_state_keeps_one_row(self):
        self.run_command('on')
        self.run_command('on')
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertTrue(SiteSettings.skipping_enabled())

    def test_command_reports_the_state(self):
        self.assertIn('OFF', self.run_command())
        self.assertFalse(SiteSettings.objects.exists())
        self.assertIn('ON', self.run_command('on'))
        self.assertIn('ON', self.run_command())
        self.assertIn('OFF', self.run_command('off'))

    def test_command_rejects_other_values(self):
        with self.assertRaises(CommandError):
            self.run_command('maybe')


class AdminUserListTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('admin@example.com', 'admin@example.com', 'pw'))
        now = timezone.now()
        for i, days_ago in enumerate([3, 1, 2]):
            u = User.objects.create_user(f'u{i}@example.com', f'u{i}@example.com', 'pw')
            User.objects.filter(pk=u.pk).update(date_joined=now - timedelta(days=days_ago))
            UserProfile.objects.create(user=u)

    def names_in_order(self, html, names):
        return sorted(names, key=html.index)

    def test_users_sortable_by_date_joined(self):
        names = ['u0@example.com', 'u1@example.com', 'u2@example.com']
        # Columns: 1 username, 2 email, 3 first, 4 last, 5 staff, 6 date joined
        newest_first = self.client.get('/admin/auth/user/?o=-6').content.decode()
        self.assertIn('Date joined', newest_first)
        self.assertEqual(self.names_in_order(newest_first, names), ['u1@example.com', 'u2@example.com', 'u0@example.com'])
        oldest_first = self.client.get('/admin/auth/user/?o=6').content.decode()
        self.assertEqual(self.names_in_order(oldest_first, names), ['u0@example.com', 'u2@example.com', 'u1@example.com'])

    def test_profiles_sortable_by_date_joined(self):
        names = ['u0@example.com', 'u1@example.com', 'u2@example.com']
        # Columns: 1 user, 2 name, 3 address, 4 email, 5 start code, 6 date joined
        newest_first = self.client.get('/admin/ui/userprofile/?o=-6').content.decode()
        self.assertEqual(self.names_in_order(newest_first, names), ['u1@example.com', 'u2@example.com', 'u0@example.com'])


class AdminListDefaultsTests(TestCase):
    """Users and User Profiles open newest first, showing only active users."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser('admin@example.com', 'admin@example.com', 'pw'))
        now = timezone.now()
        for name, days_ago, active in [('old', 9, True), ('new', 1, True), ('mid', 5, True), ('gone', 3, False)]:
            u = User.objects.create_user(f'{name}@example.com', f'{name}@example.com', 'pw', is_active=active)
            User.objects.filter(pk=u.pk).update(date_joined=now - timedelta(days=days_ago))
            UserProfile.objects.create(user=u)
        User.objects.filter(username='admin@example.com').update(date_joined=now - timedelta(days=30))

    def rows(self, url):
        html = self.client.get(url).content.decode()
        names = ['new@example.com', 'gone@example.com', 'mid@example.com', 'old@example.com']
        return [n for n in sorted(names, key=lambda n: html.find(f'>{n}</a>')) if f'>{n}</a>' in html]

    def test_users_open_newest_first_active_only(self):
        self.assertEqual(self.rows('/admin/auth/user/'), ['new@example.com', 'mid@example.com', 'old@example.com'])

    def test_profiles_open_newest_first_active_only(self):
        self.assertEqual(self.rows('/admin/ui/userprofile/'), ['new@example.com', 'mid@example.com', 'old@example.com'])

    def test_all_and_inactive_choices(self):
        for url in ('/admin/auth/user/', '/admin/ui/userprofile/'):
            self.assertEqual(self.rows(url + '?active=all'),
                             ['new@example.com', 'gone@example.com', 'mid@example.com', 'old@example.com'])
            self.assertEqual(self.rows(url + '?active=no'), ['gone@example.com'])

    def test_filter_shows_yes_selected_by_default(self):
        html = self.client.get('/admin/auth/user/').content.decode()
        self.assertRegex(html, r'<li class="selected">\s*<a href="\?active=yes"[^>]*>Yes</a>')

    def test_counts(self):
        html = self.client.get('/admin/auth/user/?_facets=True').content.decode()
        # 4 active users (incl. admin), 1 inactive, 5 in total
        for label in ('Yes (4)', 'No (1)', 'All (5)'):
            self.assertIn(label, html)


TELEGRAM = dict(TELEGRAM_BOT_TOKEN='123:TEST-TOKEN', TELEGRAM_BOT_USERNAME='bbtest_bot',
                TELEGRAM_WEBHOOK_SECRET='webhook-secret-123456')


class SentTelegramMixin:
    """Replaces the Bot API call; self.telegram_sent collects (chat_id, text)."""

    def setUp(self):
        super().setUp()
        self.telegram_sent = []
        patcher = mock.patch.object(telegram, 'send_message',
                                    side_effect=lambda chat_id, text: self.telegram_sent.append((chat_id, text)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def deliver(self):
        """Sends the next queued message and returns its text."""
        self.assertTrue(message_queue.process_one())
        return self.telegram_sent[-1][1]


class MessageQueueTests(SentTelegramMixin, TestCase):
    def enqueue(self, chat_id=111):
        message_queue.enqueue_message(chat_id=chat_id, text='<b>Hi</b>')
        return QueuedMessage.objects.latest('id')

    def test_enqueue_stores_and_process_one_sends(self):
        message = self.enqueue()
        self.assertEqual(message.status, QueuedMessage.PENDING)
        self.assertTrue(message_queue.process_one())
        self.assertEqual(self.telegram_sent, [(111, '<b>Hi</b>')])
        message.refresh_from_db()
        self.assertEqual(message.status, QueuedMessage.SENT)
        self.assertFalse(message_queue.process_one())

    def test_failure_schedules_retry_then_gives_up(self):
        message = self.enqueue()
        with mock.patch.object(telegram, 'send_message', side_effect=RuntimeError('boom')):
            for attempt in range(1, message_queue._MAX_ATTEMPTS + 1):
                self.assertTrue(message_queue.process_one())
                message.refresh_from_db()
                self.assertEqual(message.attempts, attempt)
                if message.status == QueuedMessage.PENDING:
                    self.assertGreater(message.next_attempt_at, timezone.now())
                    # Not due yet, so nothing to send
                    self.assertFalse(message_queue.process_one())
                    QueuedMessage.objects.filter(id=message.id).update(next_attempt_at=timezone.now())
        self.assertEqual(message.status, QueuedMessage.FAILED)
        self.assertIn('boom', message.last_error)

    def test_first_retry_after_20_seconds(self):
        message = self.enqueue()
        with mock.patch.object(telegram, 'send_message', side_effect=RuntimeError('boom')):
            before = timezone.now()
            message_queue.process_one()
        message.refresh_from_db()
        self.assertAlmostEqual((message.next_attempt_at - before).total_seconds(), 20, delta=1)

    def test_waits_as_long_as_telegram_asks(self):
        message = self.enqueue()
        error = telegram.TelegramError('Too Many Requests: retry after 7', retry_after=7)
        with mock.patch.object(telegram, 'send_message', side_effect=error):
            before = timezone.now()
            message_queue.process_one()
        message.refresh_from_db()
        self.assertEqual(message.status, QueuedMessage.PENDING)
        self.assertAlmostEqual((message.next_attempt_at - before).total_seconds(), 7, delta=1)

    def test_blocked_bot_fails_at_once_and_disconnects_the_chat(self):
        user = User.objects.create_user('student@example.com', password='pw')
        UserProfile.objects.create(user=user, telegram_chat_id=111, telegram_chat_name='Ana')
        message = self.enqueue(chat_id=111)
        error = telegram.TelegramError('Forbidden: bot was blocked by the user', permanent=True)
        with mock.patch.object(telegram, 'send_message', side_effect=error):
            message_queue.process_one()
        message.refresh_from_db()
        self.assertEqual((message.status, message.attempts), (QueuedMessage.FAILED, 1))
        profile = UserProfile.objects.get(user=user)
        self.assertEqual((profile.telegram_chat_id, profile.telegram_chat_name), (None, ''))

    def test_message_queued_during_check_is_not_slept_through(self):
        # A message arrives while the sender is checking the table and finds
        # nothing: the sender must not then sleep for the whole poll interval
        def check_while_message_arrives():
            message_queue._wake.set()      # what enqueue_message() does
            return False                   # the check itself saw no message yet
        with mock.patch.object(message_queue, 'process_one', side_effect=check_while_message_arrives), \
                mock.patch.object(message_queue, '_POLL_SECONDS', 5):
            started = time.monotonic()
            message_queue._work_then_wait()
            self.assertLess(time.monotonic() - started, 1)

    def test_message_claimed_by_another_worker_is_skipped(self):
        message = self.enqueue()
        QueuedMessage.objects.filter(id=message.id).update(status=QueuedMessage.SENDING, claimed_at=timezone.now())
        self.assertFalse(message_queue.process_one())

    def test_stale_claim_is_retried(self):
        message = self.enqueue()
        QueuedMessage.objects.filter(id=message.id).update(
            status=QueuedMessage.SENDING, claimed_at=timezone.now() - timedelta(minutes=10))
        self.assertTrue(message_queue.process_one())
        message.refresh_from_db()
        self.assertEqual(message.status, QueuedMessage.SENT)

    @override_settings(MESSAGE_SENDER_PROCESSES=9)
    def test_rate_budget_is_split_across_processes(self):
        self.assertLessEqual(message_queue._per_process_rps() * settings.MESSAGE_SENDER_PROCESSES,
                             message_queue._MAX_RPS_TOTAL)


@override_settings(**TELEGRAM)
class TelegramApiTests(TestCase):
    def response(self, body):
        r = mock.MagicMock()
        r.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
        return r

    def http_error(self, code, body):
        return urllib.error.HTTPError('https://api.telegram.org/x', code, 'error', {},
                                      io.BytesIO(json.dumps(body).encode()))

    def test_send_message(self):
        with mock.patch('urllib.request.urlopen', return_value=self.response({'ok': True, 'result': {'message_id': 5}})) as urlopen:
            self.assertEqual(telegram.send_message(111, '<b>Hi</b>'), {'message_id': 5})
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, 'https://api.telegram.org/bot123:TEST-TOKEN/sendMessage')
        self.assertEqual(json.loads(request.data), {'chat_id': 111, 'text': '<b>Hi</b>', 'parse_mode': 'HTML',
                                                    'disable_web_page_preview': True})

    def test_blocked_is_permanent(self):
        with mock.patch('urllib.request.urlopen', side_effect=self.http_error(403, {
                'ok': False, 'description': 'Forbidden: bot was blocked by the user'})):
            with self.assertRaises(telegram.TelegramError) as caught:
                telegram.send_message(111, 'x')
        self.assertTrue(caught.exception.permanent)
        self.assertIn('blocked', str(caught.exception))

    def test_rate_limit_passes_retry_after(self):
        with mock.patch('urllib.request.urlopen', side_effect=self.http_error(429, {
                'ok': False, 'description': 'Too Many Requests', 'parameters': {'retry_after': 12}})):
            with self.assertRaises(telegram.TelegramError) as caught:
                telegram.send_message(111, 'x')
        self.assertEqual((caught.exception.permanent, caught.exception.retry_after), (False, 12))

    def test_network_error_never_shows_the_token(self):
        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('timed out')):
            with self.assertRaises(telegram.TelegramError) as caught:
                telegram.send_message(111, 'x')
        self.assertNotIn('TEST-TOKEN', str(caught.exception))
        self.assertFalse(caught.exception.permanent)

    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_missing_token(self):
        with self.assertRaises(telegram.TelegramError):
            telegram.send_message(111, 'x')


class UpdateProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        self.client.force_login(self.user)

    def test_valid_profile_saves(self):
        self.client.post('/update-profile/', {'full_name': 'Ana Lee', 'test_center_address': 'Here'})
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual((profile.full_name, profile.test_center_address), ('Ana Lee', 'Here'))

    def test_old_contact_email_is_kept_and_not_editable(self):
        UserProfile.objects.create(user=self.user, contact_email='old@example.com')
        self.client.post('/update-profile/', {'full_name': 'Ana Lee', 'test_center_address': 'Here',
                                              'contact_email': 'new@example.com'})
        self.assertEqual(UserProfile.objects.get(user=self.user).contact_email, 'old@example.com')

    def test_invalid_profile_shows_error_on_dashboard(self):
        response = self.client.post('/update-profile/', {'full_name': 'A' * 300, 'test_center_address': 'Here'},
                                    follow=True)
        self.assertContains(response, 'Profile not saved.')
        self.assertContains(response, 'Full name')
        self.assertFalse(UserProfile.objects.get(user=self.user).full_name)


class StartCodeTests(SentTelegramMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        self.profile = UserProfile.objects.create(user=self.user, full_name='Ana Lee', telegram_chat_id=111)
        self.client.force_login(self.user)

    def post(self, code):
        return self.client.post('/start-code/', {'full_code': code})

    def test_valid_code_clears_tutor_finished(self):
        self.profile.tutor_finished_at = timezone.now()
        self.profile.save()
        self.post('123456')
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.tutor_finished_at)

    def test_valid_code_sends_telegram_message(self):
        self.assertRedirects(self.post('123456'), '/test/', fetch_redirect_response=False)
        message = QueuedMessage.objects.get()
        self.assertEqual((message.user, message.chat_id, message.category), (self.user, 111, 'start_code'))
        self.assertEqual(self.deliver(), 'Start code for <b>Ana Lee</b>: <code>123456</code>\n'
                                         'Enter it on the Start Code screen to begin the practice test.')
        self.assertEqual(self.telegram_sent[0][0], 111)

    def test_no_telegram_chat_no_message(self):
        self.profile.telegram_chat_id = None
        self.profile.save()
        self.assertRedirects(self.post('123456'), '/test/', fetch_redirect_response=False)
        self.assertFalse(QueuedMessage.objects.exists())
        self.assertEqual(UserProfile.objects.get(user=self.user).start_code, '123456')

    def test_invalid_codes_are_rejected(self):
        for code in ['12345', '1234567', '123456\n', 'abcdef', '12345\n', 'Click here http://evil.example', '']:
            response = self.post(code)
            self.assertEqual(response.status_code, 200, code)
            self.assertContains(response, 'display: block')
        self.assertFalse(QueuedMessage.objects.exists())
        self.assertIsNone(UserProfile.objects.get(user=self.user).start_code)

    def test_messages_capped_per_hour(self):
        for _ in range(views.START_CODE_MESSAGES_PER_HOUR + 3):
            self.assertRedirects(self.post('123456'), '/test/', fetch_redirect_response=False)
        self.assertEqual(QueuedMessage.objects.count(), views.START_CODE_MESSAGES_PER_HOUR)

        # Older messages no longer count
        QueuedMessage.objects.update(created_at=timezone.now() - timedelta(hours=2))
        self.post('123456')
        self.assertEqual(QueuedMessage.objects.count(), views.START_CODE_MESSAGES_PER_HOUR + 1)


class AdminUserProfileFilterTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('admin@example.com', 'admin@example.com', 'pw'))
        group = Group.objects.create(name='Morning class')
        active = User.objects.create_user('active@example.com', 'active@example.com', 'pw')
        inactive = User.objects.create_user('inactive@example.com', 'inactive@example.com', 'pw', is_active=False)
        staff = User.objects.create_user('staff@example.com', 'staff@example.com', 'pw', is_staff=True)
        active.groups.add(group)
        User.objects.filter(pk=inactive.pk).update(date_joined=timezone.now().replace(year=2025))
        self.connected = UserProfile.objects.create(user=active, full_name='Ana Lee', telegram_chat_id=111,
                                                    telegram_chat_name='Dana (@dana_parent)')
        UserProfile.objects.create(user=inactive)
        UserProfile.objects.create(user=staff)
        self.group = group

    def listed(self, query=''):
        html = self.client.get('/admin/ui/userprofile/' + query).content.decode()
        # Match the username as the list's link text: "active@example.com" is
        # also part of "inactive@example.com"
        return {n for n in ('active@example.com', 'inactive@example.com', 'staff@example.com') if f'>{n}</a>' in html}

    def test_filters_match_users_page(self):
        html = self.client.get('/admin/ui/userprofile/').content.decode()
        for title in ('By staff status', 'By superuser status', 'By active', 'By groups'):
            self.assertIn(title, html)

    def test_filtering(self):
        self.assertEqual(self.listed('?active=no'), {'inactive@example.com'})
        self.assertEqual(self.listed('?user__is_staff__exact=1'), {'staff@example.com'})
        self.assertEqual(self.listed(f'?user__groups__id__exact={self.group.pk}'), {'active@example.com'})

    def test_date_bar(self):
        self.assertEqual(self.listed('?user__date_joined__year=2025&active=all'), {'inactive@example.com'})

    def test_search_covers_account_and_profile(self):
        self.assertEqual(self.listed('?q=staff@'), {'staff@example.com'})
        self.assertEqual(self.listed('?q=Ana Lee'), {'active@example.com'})
        self.assertEqual(self.listed('?q=dana_parent'), {'active@example.com'})

    def test_telegram_column(self):
        html = self.client.get('/admin/ui/userprofile/').content.decode()
        self.assertIn('Telegram', html)
        self.assertIn('icon-yes.svg', html)      # connected tick for Ana

    @override_settings(TELEGRAM_BOT_USERNAME='bbtest_bot')
    def test_change_page_shows_connect_link_and_read_only_email(self):
        html = self.client.get(f'/admin/ui/userprofile/{self.connected.pk}/change/').content.decode()
        self.connected.refresh_from_db()
        self.assertIn(f'https://t.me/bbtest_bot?start={self.connected.telegram_link_token}', html)
        self.assertNotIn('name="contact_email"', html)
        self.assertNotIn('name="telegram_link_token"', html)


class UnscheduledBreakTelegramTests(SentTelegramMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        self.profile = UserProfile.objects.create(user=self.user, full_name='Ana Lee', telegram_chat_id=111)
        self.client.force_login(self.user)

    def post(self, **data):
        return self.client.post('/api/unscheduled-break/', data)

    def test_start_message(self):
        self.assertEqual(self.post(event='start', module=1, time_left=754).json(), {'status': 'ok'})
        self.assertEqual(self.deliver(),
                         '<b>Ana Lee</b> started an unscheduled break during Section 1, Module 2: Reading and Writing.\n'
                         'Time left in the module: <b>12:34</b>. The module timer keeps running during the break.')
        self.assertEqual(QueuedMessage.objects.get().category, 'unscheduled_break')

    def test_end_message(self):
        self.post(event='end', module=2, time_left=554, break_seconds=200)
        self.assertEqual(self.deliver(),
                         '<b>Ana Lee</b> ended their unscheduled break after <b>3 min 20 s</b>.\n'
                         "They're back to Section 2, Module 1: Math with <b>9:14</b> left in the module.")

    def test_end_message_when_module_time_ran_out(self):
        self.post(event='end', module=0, time_left=0, break_seconds=45)
        text = self.deliver()
        self.assertIn('after <b>45 s</b>', text)
        self.assertIn('ran out during the break', text)

    def test_name_is_escaped(self):
        self.profile.full_name = "Ana\nO'Neil <b>"
        self.profile.save()
        self.post(event='start', module=0, time_left=60)
        self.assertTrue(self.deliver().startswith('<b>Ana O&#x27;Neil &lt;b&gt;</b> started'))

    def test_falls_back_to_username(self):
        self.profile.full_name = ''
        self.profile.save()
        self.post(event='start', module=0, time_left=60)
        self.assertTrue(self.deliver().startswith('<b>student@example.com</b> started'))

    def test_no_telegram_chat_no_message(self):
        self.profile.telegram_chat_id = None
        self.profile.save()
        self.assertEqual(self.post(event='start', module=0, time_left=60).json(), {'status': 'no telegram chat'})
        self.assertFalse(QueuedMessage.objects.exists())

    def test_invalid_input_rejected(self):
        for data in [dict(event='pause', module=0, time_left=1), dict(event='start', module=4, time_left=1),
                     dict(event='start', module=0, time_left=-1), dict(event='start', module=0, time_left=99999),
                     dict(event='start', module='x', time_left=1), dict(event='end', module=0, time_left=1, break_seconds=-5),
                     dict(event='start')]:
            self.assertEqual(self.post(**data).status_code, 400, data)
        self.assertFalse(QueuedMessage.objects.exists())

    def test_login_and_post_required(self):
        self.assertEqual(self.client.get('/api/unscheduled-break/').status_code, 405)
        self.client.logout()
        self.assertEqual(self.post(event='start', module=0, time_left=60).status_code, 302)
        self.assertFalse(QueuedMessage.objects.exists())

    def test_capped_per_hour_separately_from_start_codes(self):
        for _ in range(views.UNSCHEDULED_BREAK_MESSAGES_PER_HOUR + 2):
            self.post(event='start', module=0, time_left=60)
        self.assertEqual(QueuedMessage.objects.filter(category='unscheduled_break').count(),
                         views.UNSCHEDULED_BREAK_MESSAGES_PER_HOUR)
        # Start codes still go out
        self.client.post('/start-code/', {'full_code': '123456'})
        self.assertEqual(QueuedMessage.objects.filter(category='start_code').count(), 1)


class NoteTelegramTests(SentTelegramMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('student@example.com', 'student@example.com', 'pw')
        self.profile = UserProfile.objects.create(user=self.user, full_name='Ana Lee', telegram_chat_id=111)
        self.client.force_login(self.user)

    def post(self, **data):
        return self.client.post('/api/note/', data)

    def test_note_message(self):
        self.assertEqual(self.post(module=1, question=4, text='Check the second paragraph').json(), {'status': 'ok'})
        self.assertEqual(self.deliver(),
                         '<b>Ana Lee</b> wrote a note on Question 5 in Section 1, Module 2: Reading and Writing:\n'
                         '<blockquote>Check the second paragraph</blockquote>')
        self.assertEqual(QueuedMessage.objects.get().category, 'note')

    def test_line_breaks_kept_and_html_escaped(self):
        self.post(module=0, question=0, text="line one\n<script>alert(1)</script> & it's")
        text = self.deliver()
        self.assertIn('<blockquote>line one\n&lt;script&gt;alert(1)&lt;/script&gt; &amp; it&#x27;s</blockquote>', text)
        self.assertNotIn('<script>', text)

    def test_long_note_is_cut(self):
        self.post(module=0, question=0, text='n' * 5000)
        text = self.deliver()
        self.assertIn('n' * views.NOTE_MAX_CHARS + '…', text)
        self.assertNotIn('n' * (views.NOTE_MAX_CHARS + 1), text)

    def test_highlighted_text_is_ignored(self):
        # Pages opened before this change still send the highlight; it must not reach the message
        self.post(module=0, question=0, quote='SECRET HIGHLIGHT', text='my note')
        self.assertNotIn('SECRET HIGHLIGHT', self.deliver())

    def test_question_must_exist_in_that_module(self):
        self.assertEqual(self.post(module=0, question=26, text='ok').status_code, 200)   # last of 27
        self.assertEqual(self.post(module=1, question=27, text='x').status_code, 400)    # module 2 has 27
        self.assertEqual(self.post(module=2, question=22, text='x').status_code, 400)    # math has 22
        self.assertEqual(self.post(module=3, question=21, text='ok').status_code, 200)
        for data in [dict(module=4, question=0, text='x'), dict(module=0, question=-1, text='x'),
                     dict(module='a', question=0, text='x'), dict(module=0, question=0, text='   '),
                     dict(module=0, question=0)]:
            self.assertEqual(self.post(**data).status_code, 400, data)
        self.assertEqual(QueuedMessage.objects.count(), 2)

    def test_no_telegram_chat_no_message(self):
        self.profile.telegram_chat_id = None
        self.profile.save()
        self.assertEqual(self.post(module=0, question=0, text='hi').json(), {'status': 'no telegram chat'})
        self.assertFalse(QueuedMessage.objects.exists())

    def test_capped_per_hour_separately(self):
        for _ in range(views.NOTE_MESSAGES_PER_HOUR + 2):
            self.post(module=0, question=0, text='hi')
        self.assertEqual(QueuedMessage.objects.filter(category='note').count(), views.NOTE_MESSAGES_PER_HOUR)
        self.client.post('/api/unscheduled-break/', dict(event='start', module=0, time_left=60))
        self.assertEqual(QueuedMessage.objects.filter(category='unscheduled_break').count(), 1)

    def test_login_and_post_required(self):
        self.assertEqual(self.client.get('/api/note/').status_code, 405)
        self.client.logout()
        self.assertEqual(self.post(module=0, question=0, text='hi').status_code, 302)
        self.assertFalse(QueuedMessage.objects.exists())


@override_settings(**TELEGRAM)
class TelegramWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', password='pw')
        self.profile = UserProfile.objects.create(user=self.user, full_name='Ana Lee', telegram_link_token='tok_ABC-123')

    def update(self, text, chat=None, secret='webhook-secret-123456'):
        chat = chat or {'id': 555, 'type': 'private', 'first_name': 'Dana', 'username': 'dana_parent'}
        headers = {'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN': secret} if secret is not None else {}
        return self.client.post('/telegram/webhook/', json.dumps({'update_id': 1, 'message': {'text': text, 'chat': chat}}),
                                content_type='application/json', **headers)

    def test_start_with_token_links_the_chat(self):
        response = self.update('/start tok_ABC-123')
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.telegram_chat_id, self.profile.telegram_chat_name), (555, 'Dana (@dana_parent)'))
        reply = response.json()
        self.assertEqual((reply['method'], reply['chat_id'], reply['parse_mode']), ('sendMessage', 555, 'HTML'))
        self.assertIn('<b>Ana Lee</b>', reply['text'])

    def test_new_chat_replaces_the_old_one(self):
        self.update('/start tok_ABC-123')
        self.update('/start tok_ABC-123', chat={'id': 777, 'type': 'private', 'first_name': 'Ana'})
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.telegram_chat_id, self.profile.telegram_chat_name), (777, 'Ana'))

    def test_group_chat_command_with_bot_name(self):
        self.update('/start@bbtest_bot tok_ABC-123', chat={'id': -100, 'type': 'group', 'title': 'Lee family'})
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.telegram_chat_id, self.profile.telegram_chat_name), (-100, 'Lee family'))

    def test_start_without_or_with_wrong_token(self):
        for text in ('/start', '/start nope'):
            reply = self.update(text).json()
            self.assertIn('open the connect link the student sent you', reply['text'])
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.telegram_chat_id)

    def test_stop_unlinks(self):
        self.update('/start tok_ABC-123')
        reply = self.update('/stop').json()
        self.assertIn('Disconnected', reply['text'])
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.telegram_chat_id, self.profile.telegram_chat_name), (None, ''))
        self.assertIn("isn't connected", self.update('/stop').json()['text'])

    def test_other_messages_are_ignored(self):
        response = self.update('hello')
        self.assertEqual((response.status_code, response.content), (200, b''))

    def test_secret_is_required(self):
        for secret in (None, 'wrong'):
            self.assertEqual(self.update('/start tok_ABC-123', secret=secret).status_code, 403)
        with override_settings(TELEGRAM_WEBHOOK_SECRET=''):
            self.assertEqual(self.update('/start tok_ABC-123', secret='').status_code, 403)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.telegram_chat_id)

    def press(self, data, chat_id=555, message_id=42):
        update = {'update_id': 2, 'callback_query': {'id': 'cb1', 'data': data,
                                                       'message': {'message_id': message_id, 'chat': {'id': chat_id}}}}
        with mock.patch.object(telegram, 'call') as call:
            response = self.client.post('/telegram/webhook/', json.dumps(update), content_type='application/json',
                                        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='webhook-secret-123456')
        call.assert_called_once_with('answerCallbackQuery', {'callback_query_id': 'cb1'}, timeout=5)
        return response

    def test_finished_marks_the_only_linked_student(self):
        self.update('/start tok_ABC-123')
        reply = self.update('/finished').json()
        self.assertEqual(reply['text'], 'Got it: the practice test for <b>Ana Lee</b> is marked as finished.')
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.tutor_finished_at)

    def test_finished_in_an_unlinked_chat(self):
        self.assertIn("isn't connected", self.update('/finished').json()['text'])
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.tutor_finished_at)

    def test_finished_with_several_students_asks_which(self):
        self.update('/start tok_ABC-123')
        ben = UserProfile.objects.create(user=User.objects.create_user('ben@example.com', password='pw'),
                                         full_name='Ben Kim', telegram_chat_id=555)
        reply = self.update('/finished').json()
        self.assertEqual(reply['text'], 'Which student finished the practice test?')
        self.assertEqual(reply['reply_markup']['inline_keyboard'],
                         [[{'text': 'Ana Lee', 'callback_data': f'finished:{self.profile.id}'}],
                          [{'text': 'Ben Kim', 'callback_data': f'finished:{ben.id}'}]])
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.tutor_finished_at)    # nothing marked until a button is pressed

        reply = self.press(f'finished:{ben.id}').json()
        self.assertEqual((reply['method'], reply['chat_id'], reply['message_id']), ('editMessageText', 555, 42))
        self.assertIn('<b>Ben Kim</b>', reply['text'])
        ben.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertIsNotNone(ben.tutor_finished_at)
        self.assertIsNone(self.profile.tutor_finished_at)

    def test_button_from_another_chat_is_ignored(self):
        self.update('/start tok_ABC-123')
        for data in (f'finished:{self.profile.id}', 'finished:abc', 'other:1'):
            response = self.press(data, chat_id=999 if data.endswith(str(self.profile.id)) else 555)
            self.assertEqual((response.status_code, response.content), (200, b''))
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.tutor_finished_at)

    def test_bad_requests(self):
        self.assertEqual(self.client.get('/telegram/webhook/').status_code, 405)
        response = self.client.post('/telegram/webhook/', 'not json', content_type='application/json',
                                    HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='webhook-secret-123456')
        self.assertEqual(response.status_code, 400)


@override_settings(**TELEGRAM)
class DashboardTelegramTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', password='pw')
        self.profile = UserProfile.objects.create(user=self.user, full_name='Ana Lee')
        self.client.force_login(self.user)

    def dashboard(self):
        return self.client.get('/dashboard/').content.decode()

    def test_not_connected_shows_connect_link(self):
        html = self.dashboard()
        self.profile.refresh_from_db()
        token = self.profile.telegram_link_token
        self.assertRegex(token, r'^[A-Za-z0-9_-]{20,64}$')
        self.assertIn(f'https://t.me/bbtest_bot?start={token}', html)
        self.assertIn('Not connected yet.', html)
        # Shown to copy and send to the assistant, not as a link to open
        self.assertIn(f'id="telegram-link" value="https://t.me/bbtest_bot?start={token}" readonly', html)
        self.assertIn('id="copy-telegram-link"', html)
        self.assertIn('send it to your', html)
        self.assertNotIn(f'href="https://t.me/', html)
        # The same link every time
        self.dashboard()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.telegram_link_token, token)

    def test_connected_shows_chat(self):
        self.profile.telegram_chat_id, self.profile.telegram_chat_name = 555, 'Dana (@dana_parent)'
        self.profile.save()
        html = self.dashboard()
        self.assertIn('Connected to <strong>Dana (@dana_parent)</strong>', html)
        self.assertIn('id="copy-telegram-link"', html)

    @override_settings(TELEGRAM_BOT_USERNAME='')
    def test_without_bot_username(self):
        self.assertIn("Telegram isn't set up on this site yet.", self.dashboard())

    def test_contact_email_field_is_gone(self):
        self.assertNotIn('name="contact_email"', self.dashboard())

    def test_account_without_profile_gets_one_and_can_connect(self):
        user = User.objects.create_user('admin-made@example.com', password='pw')
        self.client.force_login(user)
        html = self.dashboard()
        profile = UserProfile.objects.get(user=user)
        self.assertIn(f'https://t.me/bbtest_bot?start={profile.telegram_link_token}', html)
        self.assertNotIn("isn't set up", html)


class TelegramSetupCommandTests(TestCase):
    def run_command(self, *args):
        out = io.StringIO()
        call_command('telegram_setup', *args, stdout=out)
        return out.getvalue()

    @override_settings(TELEGRAM_BOT_TOKEN='')
    def test_without_token(self):
        self.assertIn('skipping', self.run_command('--if-configured'))
        with self.assertRaises(CommandError):
            self.run_command()

    @override_settings(TELEGRAM_BOT_TOKEN='123:T', TELEGRAM_WEBHOOK_SECRET='short')
    def test_rejects_weak_secret(self):
        with self.assertRaises(CommandError):
            self.run_command()

    @override_settings(ALLOWED_HOSTS=['localhost', 'bbtest.shop'], **TELEGRAM)
    def test_sets_webhook(self):
        results = {'getMe': {'username': 'bbtest_bot'},
                   'setWebhook': True, 'setMyCommands': True,
                   'getWebhookInfo': {'url': 'https://bbtest.shop/telegram/webhook/'}}
        with mock.patch.object(telegram, 'call', side_effect=lambda method, payload: results[method]) as call:
            out = self.run_command()
        call.assert_any_call('setWebhook', {'url': 'https://bbtest.shop/telegram/webhook/',
                                            'secret_token': 'webhook-secret-123456',
                                            'allowed_updates': ['message', 'callback_query']})
        commands = [c.args[1]['commands'] for c in call.call_args_list if c.args[0] == 'setMyCommands'][0]
        self.assertEqual([c['command'] for c in commands], ['finished', 'stop'])
        self.assertIn('Webhook for @bbtest_bot set to https://bbtest.shop/telegram/webhook/', out)
        self.assertNotIn('connect links', out)

    @override_settings(**{**TELEGRAM, 'TELEGRAM_BOT_USERNAME': 'other_bot'})
    def test_warns_when_username_does_not_match(self):
        results = {'getMe': {'username': 'bbtest_bot'}, 'setWebhook': True, 'setMyCommands': True,
                   'getWebhookInfo': {'url': 'x'}}
        with mock.patch.object(telegram, 'call', side_effect=lambda method, payload: results[method]):
            self.assertIn("connect links won't work", self.run_command())


@override_settings(TELEGRAM_BOT_USERNAME='bbtest_bot')
class EmailTelegramLinksCommandTests(TestCase):
    def setUp(self):
        def profile(username, contact, active=True, chat=None, name=''):
            u = User.objects.create_user(username, password='pw', is_active=active)
            return UserProfile.objects.create(user=u, full_name=name, contact_email=contact, telegram_chat_id=chat)
        self.ana = profile('ana@example.com', 'Parent@Example.org', name='Ana Lee')
        self.ben = profile('ben@example.com', 'parent@example.org ', name='Ben <Lee>')    # same parent, case/space differ
        self.cat = profile('cat@example.com', 'cat.mom@example.org')                      # no full name: username
        profile('dan@example.com', 'dan@example.org', chat=999)                           # already connected
        profile('eve@example.com', 'eve@example.org', active=False)                       # inactive
        profile('fay@example.com', '')                                                    # no contact email
        self.sent = []

    def run_command(self, *args, env=None):
        out = io.StringIO()

        def fake_urlopen(request, timeout):
            self.sent.append((request.headers, json.loads(request.data)))
            return mock.MagicMock()
        with mock.patch.dict(os.environ, env or {}), mock.patch('urllib.request.urlopen', side_effect=fake_urlopen), \
                mock.patch('ui.management.commands.email_telegram_links.time.sleep'):
            call_command('email_telegram_links', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_sends_nothing(self):
        out = self.run_command(env={'RESEND_API_KEY': 're_test'})
        self.assertIn('2 addresses for 3 students. Skipped: 1 already connected, 1 inactive, 1 without a contact email.', out)
        self.assertIn('parent@example.org: Ana Lee, Ben <Lee>', out)
        self.assertIn('Dry run: nothing sent', out)
        self.assertEqual(self.sent, [])

    def test_send_groups_by_address(self):
        out = self.run_command('--send', env={'RESEND_API_KEY': 're_test'})
        self.assertIn('Sent 2 emails.', out)
        by_address = {payload['to'][0]: (headers, payload) for headers, payload in self.sent}
        self.assertEqual(set(by_address), {'parent@example.org', 'cat.mom@example.org'})
        headers, payload = by_address['parent@example.org']
        self.assertEqual(headers['Authorization'], 'Bearer re_test')
        self.assertEqual(payload['from'], 'BB Test <messages@bbtest.shop>')
        for p in (self.ana, self.ben):
            p.refresh_from_db()
            link = f'https://t.me/bbtest_bot?start={p.telegram_link_token}'
            self.assertIn(link, payload['text'])
            self.assertIn(link, payload['html'])
        self.assertIn('Ben <Lee>: https://t.me/', payload['text'])
        self.assertIn('<strong>Ben &lt;Lee&gt;</strong>', payload['html'])
        self.assertIn('for each student', payload['text'])
        self.assertIn('cat@example.com: https://t.me/', by_address['cat.mom@example.org'][1]['text'])

    def test_include_inactive(self):
        self.assertIn('3 addresses for 4 students', self.run_command('--include-inactive'))

    def test_send_needs_api_key(self):
        with mock.patch.dict(os.environ, {'RESEND_API_KEY': ''}):
            with self.assertRaises(CommandError):
                call_command('email_telegram_links', '--send', stdout=io.StringIO())

    def test_retries_rate_limit_and_reports_failures(self):
        def http_error(code):
            return urllib.error.HTTPError('https://api.resend.com/emails', code, 'x', {}, io.BytesIO(b'{"message":"nope"}'))
        calls = {'n': 0}

        def flaky(request, timeout):
            calls['n'] += 1
            to = json.loads(request.data)['to'][0]
            if to == 'parent@example.org' and calls['n'] == 1:
                raise http_error(429)               # retried, then succeeds
            if to == 'cat.mom@example.org':
                raise http_error(422)               # real failure
            return mock.MagicMock()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {'RESEND_API_KEY': 're_test'}), mock.patch('urllib.request.urlopen', side_effect=flaky), \
                mock.patch('ui.management.commands.email_telegram_links.time.sleep'):
            call_command('email_telegram_links', '--send', stdout=out)
        self.assertIn('Sent 1 emails.', out.getvalue())
        self.assertIn('Failed: cat.mom@example.org: Resend returned HTTP 422', out.getvalue())


class TutorFinishedPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('student@example.com', password='pw')
        self.profile = UserProfile.objects.create(user=self.user, full_name='Ana Lee')
        self.client.force_login(self.user)

    def test_endpoint_and_page_follow_the_flag(self):
        self.assertEqual(self.client.get('/api/tutor-finished/').json(), {'finished': False})
        self.assertIn('var tutorFinished = false;', self.client.get('/congrats/').content.decode())
        self.profile.tutor_finished_at = timezone.now()
        self.profile.save()
        self.assertEqual(self.client.get('/api/tutor-finished/').json(), {'finished': True})
        self.assertIn('var tutorFinished = true;', self.client.get('/congrats/').content.decode())

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get('/api/tutor-finished/').status_code, 302)
