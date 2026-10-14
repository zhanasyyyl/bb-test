from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    test_center_address = models.TextField(blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)
    start_code = models.CharField(max_length=50, blank=True, null=True)
    test_completed = models.BooleanField(default=False)

    @property
    def first_name(self):
        if self.full_name:
            return self.full_name.split()[0]
        return ""

    def __str__(self):
        return self.user.username


class QueuedEmail(models.Model):
    """Outgoing email, stored so it survives worker restarts. Sent by ui.email_queue."""

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

    subject = models.CharField(max_length=255)
    message = models.TextField()
    from_email = models.CharField(max_length=255)
    recipient_list = models.JSONField()
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
        return f"{self.subject} -> {', '.join(self.recipient_list)}"


class LoginFailure(models.Model):
    """A failed sign-in, used to rate-limit password guessing per account."""

    username = models.CharField(max_length=254, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    def __str__(self):
        return f"{self.username} at {self.created_at}"
