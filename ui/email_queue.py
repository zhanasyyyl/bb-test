"""
Email queue with rate limiting for the Resend API.

Uses 19 worker threads to send emails concurrently while staying within
the Resend API rate limit of 20 requests per second.
"""

import multiprocessing
import queue
import threading
import time
from django.core.mail import send_mail

# Thread-safe FIFO queue for outgoing emails
_email_queue = queue.Queue()

# Number of concurrent worker threads (Resend limit is 20 req/s; keep 1 spare)
_NUM_WORKERS = max(1, min(4, (multiprocessing.cpu_count() * 2 + 1)))

# Global rate-limiter: track the timestamps of recent sends so the combined
# throughput of all workers never exceeds 19 requests per second.
_rate_lock = threading.Lock()
_send_times: list[float] = []
_MAX_RPS = 19  # stay safely below the 20 req/s hard limit


def _acquire_rate_slot():
    """Block until a rate-limit slot is available (sliding-window approach)."""
    while True:
        with _rate_lock:
            now = time.monotonic()
            # Discard timestamps older than 1 second
            while _send_times and _send_times[0] <= now - 1.0:
                _send_times.pop(0)
            if len(_send_times) < _MAX_RPS:
                _send_times.append(now)
                return  # slot acquired
            # Calculate how long to wait for the oldest slot to expire
            wait = _send_times[0] - (now - 1.0)
        time.sleep(wait)


def _worker():
    """Background worker: pulls emails from the queue, respects the
    global rate limit, and sends them."""
    while True:
        email_kwargs = _email_queue.get()  # blocks until an item is available
        try:
            _acquire_rate_slot()
            send_mail(**email_kwargs)
        except Exception as e:
            print(f"[email_queue] Email failed to send: {e}")
        finally:
            _email_queue.task_done()


# Start worker threads as daemons (auto-exit when the main process stops)
_worker_threads: list[threading.Thread] = []
for _i in range(_NUM_WORKERS):
    _t = threading.Thread(target=_worker, daemon=True, name=f"email-worker-{_i}")
    _t.start()
    _worker_threads.append(_t)


def enqueue_email(*, subject, message, from_email, recipient_list):
    """Add an email to the send queue. Returns immediately."""
    _email_queue.put({
        "subject": subject,
        "message": message,
        "from_email": from_email,
        "recipient_list": recipient_list,
    })
