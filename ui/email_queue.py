"""
Email queue with rate limiting for the Resend API.

Ensures at most 1 email is sent per second by processing a thread-safe
queue with a single background worker thread.
"""

import queue
import threading
import time
from django.core.mail import send_mail

# Thread-safe FIFO queue for outgoing emails
_email_queue = queue.Queue()

# Minimum interval between Resend API calls (seconds)
_MIN_INTERVAL = 1.0


def _worker():
    """Background worker: pulls emails from the queue and sends them,
    sleeping as needed to enforce the rate limit."""
    while True:
        email_kwargs = _email_queue.get()  # blocks until an item is available
        try:
            send_mail(**email_kwargs)
        except Exception as e:
            print(f"[email_queue] Email failed to send: {e}")
        finally:
            _email_queue.task_done()
        # Throttle: wait before processing the next item
        time.sleep(_MIN_INTERVAL)


# Start the worker as a daemon thread (auto-exits when the main process stops)
_worker_thread = threading.Thread(target=_worker, daemon=True)
_worker_thread.start()


def enqueue_email(*, subject, message, from_email, recipient_list):
    """Add an email to the send queue. Returns immediately."""
    _email_queue.put({
        "subject": subject,
        "message": message,
        "from_email": from_email,
        "recipient_list": recipient_list,
    })
