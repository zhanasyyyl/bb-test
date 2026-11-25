from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from . import telegram
from .models import QueuedMessage, UserProfile


class ActiveFilter(admin.SimpleListFilter):
    """"Active" filter that shows only active users until another choice is
    picked. Django's own filters always start on "All", so "All" is an
    explicit choice here and no choice means "Yes"."""

    title = 'active'
    parameter_name = 'active'
    field = 'is_active'

    def lookups(self, request, model_admin):
        return (('yes', 'Yes'), ('no', 'No'), ('all', 'All'))

    def queryset(self, request, queryset):
        value = self.value() or 'yes'
        if value == 'all':
            return queryset
        return queryset.filter(**{self.field: value == 'yes'})

    def choices(self, changelist):
        # Like SimpleListFilter.choices, minus its built-in "All" (which
        # would select nothing, i.e. our "Yes" default), keeping the counts
        # shown by "Show counts"
        current = self.value() or 'yes'
        counts = self.get_facet_queryset(changelist) if changelist.add_facets else None
        for i, (lookup, title) in enumerate(self.lookup_choices):
            if counts is not None:
                title = f"{title} ({counts.get(f'{i}__c', '-')})"
            yield {
                'selected': current == lookup,
                'query_string': changelist.get_query_string({self.parameter_name: lookup}),
                'display': title,
            }


class ProfileActiveFilter(ActiveFilter):
    field = 'user__is_active'


# Django's user list, plus a sortable "Date joined" column and a date bar for
# narrowing it down by when users were created. Opens newest first, showing
# only active users.
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = BaseUserAdmin.list_display + ('date_joined',)
    list_filter = ('is_staff', 'is_superuser', ActiveFilter, 'groups')
    date_hierarchy = 'date_joined'
    ordering = ('-date_joined',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'test_center_address', 'telegram_connected', 'start_code', 'date_joined')
    list_select_related = ('user',)
    # Same filters, date bar, search and defaults as the Users page, applied to
    # each profile's account (search also covers the profile's name and Telegram chat)
    list_filter = ('user__is_staff', 'user__is_superuser', ProfileActiveFilter, 'user__groups')
    date_hierarchy = 'user__date_joined'
    ordering = ('-user__date_joined',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'user__email',
                     'full_name', 'telegram_chat_name')
    # contact_email is no longer used; it's kept read-only so old values aren't lost
    readonly_fields = ('telegram_chat_id', 'telegram_chat_name', 'telegram_connect_link', 'tutor_finished_at',
                       'contact_email')
    exclude = ('telegram_link_token',)

    @admin.display(description='Date joined', ordering='user__date_joined')
    def date_joined(self, profile):
        return profile.user.date_joined

    @admin.display(description='Telegram', boolean=True, ordering='telegram_chat_id')
    def telegram_connected(self, profile):
        return profile.telegram_chat_id is not None

    @admin.display(description='Telegram connect link')
    def telegram_connect_link(self, profile):
        # Whoever opens it and presses Start gets this student's messages
        if not profile.pk:
            return '-'
        return telegram.connect_link(profile) or 'Set TELEGRAM_BOT_USERNAME to show the link'


@admin.register(QueuedMessage)
class QueuedMessageAdmin(admin.ModelAdmin):
    list_display = ('category', 'user', 'chat_id', 'status', 'attempts', 'created_at', 'sent_at')
    list_filter = ('status', 'category')
    list_select_related = ('user',)
    readonly_fields = ('created_at', 'sent_at', 'claimed_at', 'last_error')
