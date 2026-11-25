from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    test_center_address = models.TextField(blank=True, null=True)
    # No longer used (messages go to Telegram); kept so existing values aren't lost
    contact_email = models.EmailField(blank=True, null=True)
    start_code = models.CharField(max_length=50, blank=True, null=True)
    test_completed = models.BooleanField(default=False)
    # The Telegram chat that gets this student's messages (one per student).
    # It's linked when someone opens the student's connect link
    # (t.me/<bot>?start=<telegram_link_token>) and presses Start.
    telegram_chat_id = models.BigIntegerField(blank=True, null=True)
    telegram_chat_name = models.CharField(max_length=255, blank=True)
    telegram_link_token = models.CharField(max_length=64, blank=True, null=True, unique=True)
    # Set when the linked chat sends /finished; one confetti piece on the
    # congrats page turns black. Cleared when the student enters a start code.
    tutor_finished_at = models.DateTimeField(blank=True, null=True)

    @property
    def first_name(self):
        if self.full_name:
            return self.full_name.split()[0]
        return ""

    def __str__(self):
        return self.user.username


class QueuedMessage(models.Model):
    """Outgoing Telegram message, stored so it survives worker restarts.
    Sent by ui.message_queue."""

    PENDING = 'pending'
    SENDING = 'sending'
    SENT = 'sent'
    FAILED = 'failed'
    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (SENDING, 'Sending'),
        (SENT, 'Sent'),
        (FAILED, 'Failed'),
    ]

    # Who triggered the message, for per-user limits (None for system messages)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, null=True)
    chat_id = models.BigIntegerField(blank=True, null=True)
    # Telegram HTML (parse_mode=HTML): user-supplied text must be escaped
    text = models.TextField()
    # What the message is about; used for per-user hourly caps
    START_CODE = 'start_code'
    UNSCHEDULED_BREAK = 'unscheduled_break'
    NOTE = 'note'
    category = models.CharField(max_length=30, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'next_attempt_at'])]

    def __str__(self):
        return f"{self.category or 'message'} to chat {self.chat_id}"


class LoginFailure(models.Model):
    """A failed sign-in, used to rate-limit password guessing per account."""

    username = models.CharField(max_length=254, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"{self.username} at {self.created_at}"


class SiteSettings(models.Model):
    """Site-wide switches, kept in one row (pk=1) so all server processes share them."""

    allow_skipping = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "site settings"

    @classmethod
    def skipping_enabled(cls):
        """Whether students may skip by clicking the timer. Off until the row says otherwise."""
        return cls.objects.filter(pk=1, allow_skipping=True).exists()

    def __str__(self):
        return "Site settings"
