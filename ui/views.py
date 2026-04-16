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
        # Try to get contact email
        if hasattr(request.user, 'userprofile') and request.user.userprofile.contact_email:
            send_mail(
                subject="Bluebook Testing Code",
                message=f"This is the code - {full_code}.",
                from_email="Bluebook <onboarding@resend.dev>",
                recipient_list=[request.user.userprofile.contact_email],
            )
        return redirect('test_interface')
        
    return render(request, 'start_code.html', {'view_locked': True})

@login_required
def update_profile_view(request):
    if request.method == 'POST':
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.full_name = request.POST.get('full_name', profile.full_name)
        profile.test_center_address = request.POST.get('test_center_address', profile.test_center_address)
        profile.contact_email = request.POST.get('contact_email', profile.contact_email)
        profile.save()
    return redirect('dashboard')

import os
import json
from django.conf import settings

# Cache questions data in memory at module load
QUESTIONS_FILE = os.path.join(settings.BASE_DIR, 'ui', 'data', 'questions.json')
try:
    with open(QUESTIONS_FILE, 'r', encoding='utf-8') as f:
        all_questions = json.load(f)
    rw_questions_cached = [q for q in all_questions if q['type'] == 'rw']
    math_questions_cached = [q for q in all_questions if q['type'] == 'math']
    
    cached_modules = [
        {"title": "Section 1, Module 1: Reading and Writing", "count": 27, "questions": rw_questions_cached[0:27]},
        {"title": "Section 1, Module 2: Reading and Writing", "count": 27, "questions": rw_questions_cached[27:54]},
        {"title": "Section 2, Module 1: Math", "count": 22, "questions": math_questions_cached[0:22]},
        {"title": "Section 2, Module 2: Math", "count": 22, "questions": math_questions_cached[22:44]},
    ]
except Exception:
    cached_modules = []

@login_required
def test_interface_view(request):
    return render(request, 'test_interface.html', {
        'view_locked': True, 
        'modules_json': json.dumps(cached_modules)
    })
