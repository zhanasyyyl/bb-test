"""
Database-backed Telegram message queue with rate limiting.

Messages are stored as QueuedMessage rows, so they survive gunicorn worker
restarts. Every server process runs a few sender threads (started from
bluebook_project/wsgi.py) that claim rows from the shared table, so any
worker can deliver any queued message.

Telegram allows about 30 messages per second per bot, not per process, so the
25 msg/s budget is split evenly across MESSAGE_SENDER_PROCESSES processes.
Keep that setting in sync with `workers` in gunicorn.conf.py.
"""

import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from . import telegram
from .models import QueuedMessage, UserProfile

logger = logging.getLogger(__name__)

# Combined send rate of all processes (Telegram allows ~30/s per bot; keep a margin)
_MAX_RPS_TOTAL = 25

# Give up on a message after this many failed sends
_MAX_ATTEMPTS = 5

# A row stuck in "sending" this long belongs to a worker that died mid-send
_STALE_CLAIM = timedelta(minutes=5)

# How often idle senders check the table for messages queued by other processes
# (a message queued in this process wakes them at once)
_POLL_SECONDS = 1


def _per_process_rps():
    processes = max(1, int(getattr(settings, 'MESSAGE_SENDER_PROCESSES', 1)))
    return max(1, _MAX_RPS_TOTAL // processes)


# Wakes this process's senders as soon as a message is queued here
_wake = threading.Event()

# Sliding-window rate limiter shared by this process's sender threads
_rate_lock = threading.Lock()
_send_times: list[float] = []


def _acquire_rate_slot():
    """Block until this process may send another message."""
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
    """Atomically claim one due message, or return None if there is none.

    The conditional UPDATE means only one thread, in any process, wins a row.
    """
    now = timezone.now()
    pending = QueuedMessage.objects.filter(
        status=QueuedMessage.PENDING, next_attempt_at__lte=now,
    ).order_by('next_attempt_at').values_list('id', 'status', 'claimed_at')[:10]
    stale = QueuedMessage.objects.filter(
        status=QueuedMessage.SENDING, claimed_at__lt=now - _STALE_CLAIM,
    ).values_list('id', 'status', 'claimed_at')[:10]

    for message_id, status, claimed_at in [*pending, *stale]:
        won = QueuedMessage.objects.filter(
            id=message_id, status=status, claimed_at=claimed_at,
        ).update(status=QueuedMessage.SENDING, claimed_at=now)
        if won:
            return QueuedMessage.objects.get(id=message_id)
    return None


def process_one():
    """Send one due message. Returns True if there was one to send."""
    message = _claim_next()
    if message is None:
        return False

    _acquire_rate_slot()
    try:
        telegram.send_message(message.chat_id, message.text)
    except Exception as e:
        message.attempts += 1
        message.last_error = str(e)
        permanent = isinstance(e, telegram.TelegramError) and e.permanent
        if permanent or message.attempts >= _MAX_ATTEMPTS:
            message.status = QueuedMessage.FAILED
            logger.error("Message %s failed permanently after %s attempts: %s",
                         message.id, message.attempts, e)
            if permanent:
                # The bot was blocked or the chat is gone: disconnect it, so the
                # student sees "Not connected" and can link a chat again
                UserProfile.objects.filter(telegram_chat_id=message.chat_id).update(
                    telegram_chat_id=None, telegram_chat_name='')
        else:
            message.status = QueuedMessage.PENDING
            retry_after = getattr(e, 'retry_after', None)
            # Telegram's own wait when it rate-limits us; otherwise 20s, 40s, 80s, 160s
            delay = retry_after if retry_after else 10 * 2 ** message.attempts
            message.next_attempt_at = timezone.now() + timedelta(seconds=delay)
            logger.warning("Message %s failed (attempt %s), will retry: %s",
                           message.id, message.attempts, e)
        message.claimed_at = None
        message.save(update_fields=['attempts', 'last_error', 'status', 'next_attempt_at', 'claimed_at'])
    else:
        message.status = QueuedMessage.SENT
        message.sent_at = timezone.now()
        message.save(update_fields=['status', 'sent_at'])
    return True


def _work_then_wait():
    """One pass of a sender thread: send a due message, or idle until woken or polled."""
    close_old_connections()
    # Clear before checking, not after: a message queued after this point sets
    # the event again, so the wait below can't sleep through it
    _wake.clear()
    try:
        if process_one():
            return
    except Exception:
        # e.g. "database is locked" under SQLite write contention
        logger.exception("Message sender error")
        time.sleep(1)
        return
    _wake.wait(_POLL_SECONDS)


def _worker():
    """Background sender thread."""
    while True:
        _work_then_wait()


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
            threading.Thread(target=_worker, daemon=True, name=f"message-worker-{i}").start()
        _started = True


def enqueue_message(*, chat_id, text, user=None, category=''):
    """Store a Telegram message (HTML) for background sending. Returns immediately."""
    QueuedMessage.objects.create(user=user, chat_id=chat_id, text=text, category=category)
    _wake.set()
