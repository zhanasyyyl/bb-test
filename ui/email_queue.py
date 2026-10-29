"""
Database-backed email queue with rate limiting for the Resend API.

Emails are stored as QueuedEmail rows, so they survive gunicorn worker
restarts. Every server process runs a few sender threads (started from
bluebook_project/wsgi.py) that claim rows from the shared table, so any
worker can deliver any queued email.

Resend allows 20 requests per second per account, not per process, so the
19 req/s budget is split evenly across EMAIL_SENDER_PROCESSES processes.
Keep that setting in sync with `workers` in gunicorn.conf.py.
"""

import logging
import re
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections
from django.utils import timezone

from .models import QueuedEmail

logger = logging.getLogger(__name__)

# Combined send rate of all processes (Resend hard limit is 20 req/s; keep 1 spare)
_MAX_RPS_TOTAL = 19

# Give up on an email after this many failed sends
_MAX_ATTEMPTS = 5

# A row stuck in "sending" this long belongs to a worker that died mid-send
_STALE_CLAIM = timedelta(minutes=5)

# How often idle senders check the table for emails queued by other processes
_POLL_SECONDS = 5


def _per_process_rps():
    processes = max(1, int(getattr(settings, 'EMAIL_SENDER_PROCESSES', 1)))
    return max(1, _MAX_RPS_TOTAL // processes)


# Wakes this process's senders as soon as an email is queued here
_wake = threading.Event()

# Sliding-window rate limiter shared by this process's sender threads
_rate_lock = threading.Lock()
_send_times: list[float] = []


def _resend_tags(user):
    """Resend tags (shown on each email in the Resend dashboard).
    Tag values may only contain ASCII letters, digits, underscores and dashes."""
    def clean(value):
        return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:256]
    return [
        {"name": "user_id", "value": str(user.pk)},
        {"name": "username", "value": clean(user.get_username())},
    ]


def _acquire_rate_slot():
    """Block until this process may send another email."""
    max_rps = _per_process_rps()
    while True:
        with _rate_lock:
            now = time.monotonic()
            # Discard timestamps older than 1 second
            while _send_times and _send_times[0] <= now - 1.0:
                _send_times.pop(0)
            if len(_send_times) < max_rps:
                _send_times.append(now)
                return  # slot acquired
            # Calculate how long to wait for the oldest slot to expire
            wait = _send_times[0] - (now - 1.0)
        time.sleep(wait)


def _claim_next():
    """Atomically claim one due email, or return None if there is none.

    The conditional UPDATE means only one thread, in any process, wins a row.
    """
    now = timezone.now()
    pending = QueuedEmail.objects.filter(
        status=QueuedEmail.PENDING, next_attempt_at__lte=now,
    ).order_by('next_attempt_at').values_list('id', 'status', 'claimed_at')[:10]
    stale = QueuedEmail.objects.filter(
        status=QueuedEmail.SENDING, claimed_at__lt=now - _STALE_CLAIM,
    ).values_list('id', 'status', 'claimed_at')[:10]

    for email_id, status, claimed_at in [*pending, *stale]:
        won = QueuedEmail.objects.filter(
            id=email_id, status=status, claimed_at=claimed_at,
        ).update(status=QueuedEmail.SENDING, claimed_at=now)
        if won:
            return QueuedEmail.objects.select_related('user').get(id=email_id)
    return None


def process_one():
    """Send one due email. Returns True if there was one to send."""
    email = _claim_next()
    if email is None:
        return False

    _acquire_rate_slot()
    try:
        message = EmailMultiAlternatives(
            subject=email.subject,
            body=email.message,
            from_email=email.from_email,
            to=email.recipient_list,
        )
        if email.html_message:
            message.attach_alternative(email.html_message, "text/html")
        if email.user is not None:
            # Only Anymail backends read esp_extra; others ignore it
            message.esp_extra = {"tags": _resend_tags(email.user)}
        message.send()
    except Exception as e:
        email.attempts += 1
        email.last_error = repr(e)
        if email.attempts >= _MAX_ATTEMPTS:
            email.status = QueuedEmail.FAILED
            logger.error("Email %s failed permanently after %s attempts: %r",
                         email.id, email.attempts, e)
        else:
            email.status = QueuedEmail.PENDING
            email.next_attempt_at = timezone.now() + timedelta(seconds=30 * 2 ** email.attempts)
            logger.warning("Email %s failed (attempt %s), will retry: %r",
                           email.id, email.attempts, e)
        email.claimed_at = None
        email.save(update_fields=['attempts', 'last_error', 'status', 'next_attempt_at', 'claimed_at'])
    else:
        email.status = QueuedEmail.SENT
        email.sent_at = timezone.now()
        email.save(update_fields=['status', 'sent_at'])
    return True


def _worker():
    """Background sender: delivers due emails, then idles until woken or polled."""
    while True:
        close_old_connections()
        try:
            if process_one():
                continue
        except Exception:
            # e.g. "database is locked" under SQLite write contention
            logger.exception("Email sender error")
            time.sleep(1)
            continue
        _wake.clear()
        _wake.wait(_POLL_SECONDS)


_start_lock = threading.Lock()
_started = False


def start_workers():
    """Start this process's sender threads (idempotent).

    Called from wsgi.py, so it runs in each gunicorn worker after fork and in
    runserver, but not in management commands or tests.
    """
    global _started
    with _start_lock:
        if _started:
            return
        for i in range(_per_process_rps()):
            # Daemon threads exit with the process; unsent rows stay in the DB
            threading.Thread(target=_worker, daemon=True, name=f"email-worker-{i}").start()
        _started = True


def enqueue_email(*, subject, message, from_email, recipient_list, user=None, html_message=''):
    """Store an email for background sending. Returns immediately."""
    QueuedEmail.objects.create(
        user=user,
        subject=subject,
        message=message,
        html_message=html_message,
        from_email=from_email,
        recipient_list=list(recipient_list),
    )
    _wake.set()
