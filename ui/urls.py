from django.urls import path
from django.views.generic import TemplateView
from django.contrib.auth.decorators import login_required
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('cb_login/', views.cb_login_view, name='cb_login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('update-profile/', views.update_profile_view, name='update_profile'),
    path('start-code/', views.start_code_view, name='start_code'),
    path('test/', views.test_interface_view, name='test_interface'),
    path('api/mark-test-completed/', views.mark_test_completed_view, name='mark_test_completed'),
    path('quit/', views.dashboard_view, name='quit'),
    path('question-editor/', views.question_editor_view, name='question_editor'),
    path('api/questions/', views.api_questions, name='api_questions'),
]

# Generate setup step URLs dynamically (steps 1–8)
for i in range(1, 9):
    route = 'setup/' if i == 1 else f'setup_step{i}/'
    urlpatterns.append(
        path(route, login_required(TemplateView.as_view(template_name=f'setup_step{i}.html')), name=f'setup_step{i}')
    )

