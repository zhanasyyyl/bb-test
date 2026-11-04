from django.contrib import admin
from .models import QueuedEmail, UserProfile

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'test_center_address','contact_email', 'start_code')
    list_select_related = ('user',)


@admin.register(QueuedEmail)
class QueuedEmailAdmin(admin.ModelAdmin):
    list_display = ('subject', 'recipient_list', 'status', 'attempts', 'created_at', 'sent_at')
    list_filter = ('status',)
    readonly_fields = ('created_at', 'sent_at', 'claimed_at', 'last_error')
