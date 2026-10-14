from datetime import timedelta

from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import LoginFailure

# Failed sign-ins allowed per account within the window before it is locked.
# Limited per account, not per IP: students at a test center share one IP.
LOGIN_MAX_FAILURES = 10
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)


def _reserve_login_attempt(username):
    """Count this attempt as a failure before checking the password.

    Returns False if the account is locked. The check and the insert run in
    one transaction; with SQLite's IMMEDIATE transaction mode (see settings)
    that takes the write lock up front, so simultaneous attempts from
    different workers are serialized and can't all slip under the limit.
    """
    cutoff = timezone.now() - LOGIN_FAILURE_WINDOW
    with transaction.atomic():
        LoginFailure.objects.filter(created_at__lt=cutoff).delete()
        if LoginFailure.objects.filter(username=username).count() >= LOGIN_MAX_FAILURES:
            return False
        LoginFailure.objects.create(username=username)
        return True


class LockoutModelBackend(ModelBackend):
    """ModelBackend with per-account lockout, so every login form is covered
    (the student sign-in page and the Django admin)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        key = username.strip().lower()
        if not _reserve_login_attempt(key):
            if request is not None:
                # Lets the sign-in view show the lockout message
                request.login_locked = True
            # Stops authenticate() from trying any other backend
            raise PermissionDenied
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is not None:
            LoginFailure.objects.filter(username=key).delete()
        return user
