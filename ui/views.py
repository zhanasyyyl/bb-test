import os
import json
import functools

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.conf import settings

from .models import UserProfile
from .forms import UserProfileForm
from .email_queue import enqueue_email


def _get_profile(user):
    """Return the user's UserProfile or None."""
    return getattr(user, 'userprofile', None)


def _get_or_create_profile(user):
    """Ensure the user has a UserProfile record."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def login_view(request):
    return render(request, 'login.html')

def cb_login_view(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        if email and password:
            user = authenticate(request, username=email, password=password)
            if user is not None:
                login(request, user)
                # Reset test_completed so registration card shows on every login
                profile = _get_profile(user)
                if profile:
                    profile.test_completed = False
                    profile.save()
                return redirect('dashboard')
            else:
                return render(request, 'cb_login.html', {'error': 'Invalid email or password'})
    return render(request, 'cb_login.html')

@login_required
def dashboard_view(request):
    _get_or_create_profile(request.user)
    return render(request, 'dashboard.html')


@login_required
def start_code_view(request):
    if request.method == 'POST':
        full_code = request.POST.get('full_code', '')
        
        # Save start code to user profile
        profile = _get_or_create_profile(request.user)
        profile.start_code = full_code
        profile.save()

        # Enqueue email — processed at most 1/sec by the background worker
        if profile.contact_email:
            enqueue_email(
                subject=f"{full_code} - Bluebook Testing Code",
                message=f"This is the code - {full_code}. Enter this code in the app to start the test.",
                from_email="Bluebook <messages@bbtest.space>",
                recipient_list=[profile.contact_email],
            )

        return redirect('test_interface')
        
    return render(request, 'start_code.html', {'view_locked': True})


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
    return redirect('dashboard')


# Load questions data dynamically and cache in memory
QUESTIONS_FILE = os.path.join(settings.BASE_DIR, 'ui', 'data', 'questions.json')

@functools.lru_cache(maxsize=1)
def get_cached_modules_json():
    try:
        with open(QUESTIONS_FILE, 'r', encoding='utf-8') as f:
            all_questions = json.load(f)
        rw_questions_cached = [q for q in all_questions if q.get('type') == 'rw']
        math_questions_cached = [q for q in all_questions if q.get('type') == 'math']
        
        cached_modules = [
            {"title": "Section 1, Module 1: Reading and Writing", "count": 27, "questions": rw_questions_cached[0:27]},
            {"title": "Section 1, Module 2: Reading and Writing", "count": 27, "questions": rw_questions_cached[27:54]},
            {"title": "Section 2, Module 1: Math", "count": 22, "questions": math_questions_cached[0:22]},
            {"title": "Section 2, Module 2: Math", "count": 22, "questions": math_questions_cached[22:44]},
        ]
        return json.dumps(cached_modules)
    except Exception:
        return "[]"

@login_required
def test_interface_view(request):
    _get_or_create_profile(request.user)
    return render(request, 'test_interface.html', {
        'view_locked': True, 
        'modules_json': get_cached_modules_json()
    })


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
            return JsonResponse({'error': str(e)}, status=500)

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            with open(QUESTIONS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            # Clear the cached modules so test interface picks up changes
            get_cached_modules_json.cache_clear()
            return JsonResponse({'status': 'ok', 'count': len(data)})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)
