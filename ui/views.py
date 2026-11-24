import os
import re
import hmac
import json
import logging
import tempfile
import threading
from datetime import timedelta

from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.contrib import messages
from django.utils import timezone

from .models import QueuedMessage, SiteSettings, UserProfile
from .forms import UserProfileForm
from .message_queue import enqueue_message
from . import telegram

logger = logging.getLogger(__name__)


def _get_profile(user):
    """Return the user's UserProfile or None."""
    return getattr(user, 'userprofile', None)


def login_view(request):
    return render(request, 'login.html')

def cb_login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        context = {'email': email}
        if not email or not password:
            context['error'] = 'Enter your email and password.'
            return render(request, 'cb_login.html', context)

        user = authenticate(request, username=email, password=password)
        if user is None:
            if getattr(request, 'login_locked', False):
                context['error'] = 'Too many sign-in attempts. Try again in 15 minutes.'
                return render(request, 'cb_login.html', context, status=429)
            context['error'] = 'Your email or password is wrong. Please try again.'
            return render(request, 'cb_login.html', context)

        login(request, user)
        # Reset test_completed so registration card shows on every login
        profile = _get_profile(user)
        if profile:
            profile.test_completed = False
            profile.save()
        return redirect('dashboard')
    return render(request, 'cb_login.html')

@login_required
def dashboard_view(request):
    profile = _get_profile(request.user)
    return render(request, 'dashboard.html', {
        'telegram_link': telegram.connect_link(profile) if profile else None,
    })


START_CODE_RE = re.compile(r'[0-9]{6}')

# Messages of each kind one user can trigger per hour. High enough that people
# testing the flow repeatedly won't hit them; they only stop scripted abuse.
START_CODE_MESSAGES_PER_HOUR = 50
UNSCHEDULED_BREAK_MESSAGES_PER_HOUR = 30
NOTE_MESSAGES_PER_HOUR = 60

# Longest note put in a note message; longer ones are cut (Telegram's limit is 4096)
NOTE_MAX_CHARS = 2000


def _messages_sent_recently(user, category):
    return QueuedMessage.objects.filter(
        user=user, category=category, created_at__gte=timezone.now() - timedelta(hours=1),
    ).count()


def _display_name(profile, user):
    return ' '.join((profile.full_name or user.get_username()).split())[:80]


def _send_to_student_chat(request, profile, category, limit, template, context):
    """Queues a Telegram message to the student's linked chat. Returns a status
    for the page: no linked chat and the hourly cap both just skip sending."""
    if not profile or not profile.telegram_chat_id:
        return 'no telegram chat'
    if _messages_sent_recently(request.user, category) >= limit:
        logger.warning("%s message limit reached for user %s", category, request.user.pk)
        return 'limit reached'
    context = {'name': _display_name(profile, request.user), **context}
    enqueue_message(
        chat_id=profile.telegram_chat_id,
        text=render_to_string(f'telegram/{template}.html', context).strip(),
        user=request.user,
        category=category,
    )
    return 'ok'


@login_required
def start_code_view(request):
    if request.method == 'POST':
        full_code = request.POST.get('full_code', '')
        if not START_CODE_RE.fullmatch(full_code):
            return render(request, 'start_code.html', {'code_error': True})

        # Save start code to user profile
        profile = _get_profile(request.user)
        if profile:
            profile.start_code = full_code
            profile.save()

        # Delivered by the rate-limited background senders
        _send_to_student_chat(request, profile, QueuedMessage.START_CODE, START_CODE_MESSAGES_PER_HOUR,
                              'start_code', {'code': full_code})
        return redirect('test_interface')

    return render(request, 'start_code.html')


def _format_duration(seconds):
    """1:05 style for times left, "3 min 20 s" style for break lengths."""
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}", (f"{minutes} min {secs} s" if minutes else f"{secs} s")


@login_required
@require_POST
def unscheduled_break_view(request):
    """Messages the student's Telegram chat when an unscheduled break starts
    or ends. The page sends only numbers (module index and timings), never
    text, so the message content can't be chosen by the caller."""
    event = request.POST.get('event')
    try:
        module_index = int(request.POST.get('module', ''))
        time_left = int(request.POST.get('time_left', ''))
        break_seconds = int(request.POST.get('break_seconds', '0'))
    except ValueError:
        return JsonResponse({'error': 'Invalid input'}, status=400)
    if (event not in ('start', 'end') or not 0 <= module_index < len(MODULES)
            or not 0 <= time_left <= 7200 or not 0 <= break_seconds <= 7200):
        return JsonResponse({'error': 'Invalid input'}, status=400)

    status = _send_to_student_chat(
        request, _get_profile(request.user), QueuedMessage.UNSCHEDULED_BREAK,
        UNSCHEDULED_BREAK_MESSAGES_PER_HOUR, 'unscheduled_break', {
            'event': event,
            'module': MODULES[module_index][0],
            'time_left': _format_duration(time_left)[0],
            'break_length': _format_duration(break_seconds)[1],
            'time_ran_out': event == 'end' and time_left == 0,
        })
    return JsonResponse({'status': status})


def _shorten(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


@login_required
@require_POST
def note_view(request):
    """Messages a note the student wrote on a highlight to their Telegram
    chat. The note is free text, so it is capped per hour, cut to
    NOTE_MAX_CHARS and HTML-escaped by the template."""
    try:
        module_index = int(request.POST.get('module', ''))
        question_index = int(request.POST.get('question', ''))
    except ValueError:
        return JsonResponse({'error': 'Invalid input'}, status=400)
    text = request.POST.get('text', '').strip()
    if not 0 <= module_index < len(MODULES) or not text:
        return JsonResponse({'error': 'Invalid input'}, status=400)
    _, _, start, end = MODULES[module_index]
    if not 0 <= question_index < end - start:
        return JsonResponse({'error': 'Invalid input'}, status=400)

    status = _send_to_student_chat(
        request, _get_profile(request.user), QueuedMessage.NOTE, NOTE_MESSAGES_PER_HOUR, 'note', {
            'module': MODULES[module_index][0],
            'question': question_index + 1,
            'text': _shorten(text, NOTE_MAX_CHARS),
        })
    return JsonResponse({'status': status})


def _chat_display_name(chat):
    name = ' '.join(filter(None, [chat.get('first_name'), chat.get('last_name')])) or chat.get('title', '')
    if chat.get('username'):
        name = f"{name} (@{chat['username']})" if name else f"@{chat['username']}"
    return name[:255]


@csrf_exempt
@require_POST
def telegram_webhook_view(request):
    """Receives updates from Telegram (set up with `manage.py telegram_setup`).

    "/start <token>" comes from a student's connect link and links that chat
    to the student's profile (replacing any earlier chat); "/stop" unlinks the
    chat. Replies are returned as the webhook response, which Telegram sends.
    """
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    given = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
        return HttpResponse(status=403)
    try:
        update = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)

    message = update.get('message') or {}
    chat = message.get('chat') or {}
    chat_id = chat.get('id')
    command, _, argument = (message.get('text') or '').strip().partition(' ')
    command = command.split('@')[0]   # "/start@BotName" in group chats
    if not chat_id or command not in ('/start', '/stop'):
        return HttpResponse(status=200)

    if command == '/start':
        token = argument.strip()
        profile = (UserProfile.objects.select_related('user').filter(telegram_link_token=token).first()
                   if token else None)
        if profile:
            profile.telegram_chat_id = chat_id
            profile.telegram_chat_name = _chat_display_name(chat)
            profile.save(update_fields=['telegram_chat_id', 'telegram_chat_name'])
            reply = render_to_string('telegram/connected.html', {
                'name': _display_name(profile, profile.user)}).strip()
        else:
            reply = 'To get messages here, open the Connect Telegram link in your BB Test profile.'
    else:
        unlinked = UserProfile.objects.filter(telegram_chat_id=chat_id).update(
            telegram_chat_id=None, telegram_chat_name='')
        reply = ("Disconnected. You won't get BB Test messages here any more." if unlinked
                 else "This chat isn't connected to a BB Test profile.")
    return JsonResponse({'method': 'sendMessage', 'chat_id': chat_id, 'text': reply, 'parse_mode': 'HTML'})


@login_required
@require_POST
def mark_test_completed_view(request):
    profile = _get_profile(request.user)
    if profile:
        profile.test_completed = True
        profile.save()
    return JsonResponse({'status': 'ok'})


@login_required
@require_POST
def update_profile_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    form = UserProfileForm(request.POST, instance=profile)
    if form.is_valid():
        form.save()
    else:
        problems = [
            f"{form.fields[field].label if field in form.fields else field}: {' '.join(errors)}"
            for field, errors in form.errors.items()
        ]
        messages.error(request, "Profile not saved. " + " ".join(problems))
    return redirect('dashboard')


QUESTIONS_FILE = os.path.join(settings.BASE_DIR, 'ui', 'data', 'questions.json')

# (title, question type, start, end) — slices of each type, in file order
MODULES = [
    ("Section 1, Module 1: Reading and Writing", 'rw', 0, 27),
    ("Section 1, Module 2: Reading and Writing", 'rw', 27, 54),
    ("Section 2, Module 1: Math", 'math', 0, 22),
    ("Section 2, Module 2: Math", 'math', 22, 44),
]
REQUIRED_COUNTS = {'rw': 54, 'math': 44}

# Serializes writes to QUESTIONS_FILE within this process; os.replace makes
# each write atomic, so a concurrent save from another process can't corrupt it
_questions_write_lock = threading.Lock()

# (file mtime, modules JSON). Keyed on mtime so every worker picks up editor saves.
_modules_cache = None


def _count_by_type(questions):
    counts = {'rw': 0, 'math': 0}
    for q in questions:
        counts[q['type']] += 1
    return counts


def get_cached_modules_json():
    """Return the 4 test modules as JSON, reloading when questions.json changes.

    Raises OSError / ValueError if the file can't be read or parsed.
    """
    global _modules_cache
    mtime = os.stat(QUESTIONS_FILE).st_mtime_ns
    cached = _modules_cache
    if cached and cached[0] == mtime:
        return cached[1]

    with open(QUESTIONS_FILE, 'r', encoding='utf-8') as f:
        all_questions = json.load(f)
    by_type = {
        t: [q for q in all_questions if q.get('type') == t] for t in REQUIRED_COUNTS
    }
    for t, needed in REQUIRED_COUNTS.items():
        if len(by_type[t]) < needed:
            logger.warning("questions.json has %s %s questions; the test needs %s",
                           len(by_type[t]), t, needed)

    modules = []
    for title, qtype, start, end in MODULES:
        questions = by_type[qtype][start:end]
        modules.append({"title": title, "count": len(questions), "questions": questions})
    result = json.dumps(modules)
    _modules_cache = (mtime, result)
    return result


def validate_questions(data):
    """Return (errors, warnings) for a full questions.json payload."""
    if not isinstance(data, list):
        return ["Expected a list of questions."], []

    errors = []
    seen_ids = set()
    for i, q in enumerate(data):
        where = f"Question #{i + 1}"
        if not isinstance(q, dict):
            errors.append(f"{where}: must be an object.")
            continue
        qid = q.get('id')
        if not isinstance(qid, int) or isinstance(qid, bool):
            errors.append(f"{where}: id must be an integer.")
        elif qid in seen_ids:
            errors.append(f"{where}: duplicate id {qid}.")
        else:
            seen_ids.add(qid)
            where = f"Question id {qid}"
        if q.get('type') not in REQUIRED_COUNTS:
            errors.append(f"{where}: type must be 'rw' or 'math'.")
        if not isinstance(q.get('question'), str) or not q['question'].strip():
            errors.append(f"{where}: question text is required.")
        if 'passage' in q and not isinstance(q['passage'], str):
            errors.append(f"{where}: passage must be text.")
        if 'options' in q and not (
            isinstance(q['options'], list) and all(isinstance(o, str) for o in q['options'])
        ):
            errors.append(f"{where}: options must be a list of text.")
    if errors:
        return errors, []

    counts = _count_by_type(data)
    warnings = [
        f"Only {counts[t]} {'Reading and Writing' if t == 'rw' else 'Math'} questions; "
        f"the test needs {needed}, so some modules will be short."
        for t, needed in REQUIRED_COUNTS.items() if counts[t] < needed
    ]
    return [], warnings


def _write_questions(data):
    """Atomically replace QUESTIONS_FILE: write a temp file, then rename over it."""
    directory = os.path.dirname(QUESTIONS_FILE)
    with _questions_write_lock:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.questions-', suffix='.json')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            # mkstemp creates the file as 0600; keep the existing permissions
            if os.path.exists(QUESTIONS_FILE):
                os.chmod(tmp_path, os.stat(QUESTIONS_FILE).st_mode & 0o777)
            os.replace(tmp_path, QUESTIONS_FILE)
        except BaseException:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

@login_required
def test_interface_view(request):
    try:
        modules_json = get_cached_modules_json()
    except (OSError, ValueError):
        logger.exception("Could not load %s", QUESTIONS_FILE)
        return HttpResponse(
            "The test is temporarily unavailable. Please try again shortly.",
            status=503, content_type='text/plain',
        )
    return render(request, 'test_interface.html', {
        'modules_json': modules_json,
        'desmos_api_key': settings.DESMOS_API_KEY,
        'can_skip': request.user.is_staff or SiteSettings.skipping_enabled(),
    })


@login_required
def congrats_view(request):
    return render(request, 'congrats.html')



@login_required
def question_editor_view(request):
    if not request.user.is_superuser:
        return redirect('dashboard')
    return render(request, 'question_editor.html')


@login_required
def api_questions(request):
    if not request.user.is_superuser:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    if request.method == 'GET':
        try:
            with open(QUESTIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return JsonResponse(data, safe=False)
        except Exception as e:
            if settings.DEBUG:
                return JsonResponse({'error': str(e)}, status=500)
            return JsonResponse({'error': 'Failed to load questions'}, status=500)

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
        except ValueError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        errors, warnings = validate_questions(data)
        if errors:
            return JsonResponse({'error': 'Questions not saved', 'details': errors}, status=400)
        try:
            _write_questions(data)
            return JsonResponse({'status': 'ok', 'count': len(data), 'warnings': warnings})
        except Exception as e:
            if settings.DEBUG:
                return JsonResponse({'error': str(e)}, status=500)
            return JsonResponse({'error': 'Failed to save questions'}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)
