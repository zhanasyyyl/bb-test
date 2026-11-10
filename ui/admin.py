from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import QueuedEmail, UserProfile


# Django's user list, plus a sortable "Date joined" column and a date bar
# for narrowing the list down by when users were created
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = BaseUserAdmin.list_display + ('date_joined',)
    date_hierarchy = 'date_joined'


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'test_center_address','contact_email', 'start_code', 'date_joined')
    list_select_related = ('user',)
    # Same filters, date bar and search as the Users page, applied to each
    # profile's account (search also covers the profile's own name and email)
    list_filter = ('user__is_staff', 'user__is_superuser', 'user__is_active', 'user__groups')
    date_hierarchy = 'user__date_joined'
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email',
                     'full_name', 'contact_email')

    @admin.display(description='Date joined', ordering='user__date_joined')
    def date_joined(self, profile):
        return profile.user.date_joined


@admin.register(QueuedEmail)
class QueuedEmailAdmin(admin.ModelAdmin):
    list_display = ('subject', 'recipient_list', 'status', 'attempts', 'created_at', 'sent_at')
    list_filter = ('status',)
    readonly_fields = ('created_at', 'sent_at', 'claimed_at', 'last_error')
