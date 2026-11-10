from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import QueuedEmail, UserProfile


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
    list_display = ('user', 'full_name', 'test_center_address','contact_email', 'start_code', 'date_joined')
    list_select_related = ('user',)
    # Same filters, date bar, search and defaults as the Users page, applied to
    # each profile's account (search also covers the profile's own name and email)
    list_filter = ('user__is_staff', 'user__is_superuser', ProfileActiveFilter, 'user__groups')
    date_hierarchy = 'user__date_joined'
    ordering = ('-user__date_joined',)
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
