import os
import re
import json
import logging
import tempfile
import threading
from datetime import timedelta

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.contrib import messages
from django.utils import timezone

from .models import QueuedEmail, UserProfile
from .forms import UserProfileForm
from .email_queue import enqueue_email, user_sender

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
            context['error'] = 'Invalid email or password.'
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
    return render(request, 'dashboard.html')


START_CODE_RE = re.compile(r'[0-9]{6}')

# Start-code emails one user can trigger per hour. High enough that people
# testing the flow repeatedly won't hit it; it only stops scripted abuse
# (anyone signed in can point their contact email at any address).
START_CODE_EMAILS_PER_HOUR = 50


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

        if profile and profile.contact_email:
            sent_recently = QueuedEmail.objects.filter(
                user=request.user, created_at__gte=timezone.now() - timedelta(hours=1),
            ).count()
            if sent_recently < START_CODE_EMAILS_PER_HOUR:
                # Queue email — delivered by the rate-limited background senders
                enqueue_email(
                    subject=f"{full_code} - Bluebook Testing Code",
                    message=f"This is the code - {full_code}. Enter this code in the app to start the test.",
                    from_email=user_sender(request.user),
                    recipient_list=[profile.contact_email],
                    user=request.user,
                )
            else:
                logger.warning("Start-code email limit reached for user %s", request.user.pk)

        return redirect('test_interface')

    return render(request, 'start_code.html')


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
