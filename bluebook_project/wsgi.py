"""
WSGI config for bluebook_project project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bluebook_project.settings')

application = get_wsgi_application()

# Start the background email senders in each server process (after gunicorn forks)
from ui.email_queue import start_workers  # noqa: E402
start_workers()
