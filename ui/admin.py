from django.contrib import admin
from .models import UserProfile

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'contact_email', 'start_code')
    list_select_related = ('user',)
