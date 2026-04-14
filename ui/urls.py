from django.urls import path
from django.views.generic import TemplateView
from django.contrib.auth.decorators import login_required
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('cb_login/', views.cb_login_view, name='cb_login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('update-profile/', views.update_profile_view, name='update_profile'),
    path('setup/', login_required(TemplateView.as_view(template_name='setup_step1.html')), name='setup_step1'),
    path('setup_step2/', login_required(TemplateView.as_view(template_name='setup_step2.html')), name='setup_step2'),
    path('setup_step3/', login_required(TemplateView.as_view(template_name='setup_step3.html')), name='setup_step3'),
    path('setup_step4/', login_required(TemplateView.as_view(template_name='setup_step4.html')), name='setup_step4'),
    path('setup_step5/', login_required(TemplateView.as_view(template_name='setup_step5.html')), name='setup_step5'),
    path('setup_step6/', login_required(TemplateView.as_view(template_name='setup_step6.html')), name='setup_step6'),
    path('setup_step7/', login_required(TemplateView.as_view(template_name='setup_step7.html')), name='setup_step7'),
    path('setup_step8/', login_required(TemplateView.as_view(template_name='setup_step8.html')), name='setup_step8'),
    path('start-code/', views.start_code_view, name='start_code'),
    path('test/', views.test_interface_view, name='test_interface'),
]
