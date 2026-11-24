"""
Telegram Bot API: sending messages and linking chats to student profiles.

A student's connect link is t.me/<TELEGRAM_BOT_USERNAME>?start=<token>.
Whoever opens it and presses Start sends "/start <token>" to the bot;
Telegram passes that to telegram_webhook_view (set up with
`manage.py telegram_setup`), which links that chat to the profile.
"""

import json
import secrets
import urllib.error
import urllib.request

from django.conf import settings

API_URL = 'https://api.telegram.org/bot{token}/{method}'


class TelegramError(Exception):
    """A failed Bot API call. `permanent` means retrying can't help (the bot
    was blocked or the chat doesn't exist); `retry_after` is how long Telegram
    asked us to wait (rate limiting)."""

    def __init__(self, description, *, permanent=False, retry_after=None):
        super().__init__(description)
        self.permanent = permanent
        self.retry_after = retry_after


def call(method, payload, timeout=10):
    """Calls a Bot API method and returns its result."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise TelegramError('TELEGRAM_BOT_TOKEN is not set')
    request = urllib.request.Request(
        API_URL.format(token=token, method=method),
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except ValueError:
            body = {}
        description = body.get('description') or f'HTTP {e.code}'
        retry_after = (body.get('parameters') or {}).get('retry_after')
        # 400: chat not found / bad request, 403: bot blocked or kicked
        raise TelegramError(description, permanent=e.code in (400, 403), retry_after=retry_after) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # Never include the request URL: it contains the bot token
        raise TelegramError(f'Network error: {getattr(e, "reason", e)}') from None
    if not body.get('ok'):
        raise TelegramError(body.get('description') or 'Telegram returned ok=false')
    return body.get('result')


def send_message(chat_id, text):
    """Sends `text` (Telegram HTML) to a chat."""
    return call('sendMessage', {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    })


def connect_link(profile):
    """The profile's t.me connect link, creating its private token on first use.
    None if TELEGRAM_BOT_USERNAME isn't configured."""
    username = settings.TELEGRAM_BOT_USERNAME.lstrip('@')
    if not username:
        return None
    if not profile.telegram_link_token:
        # 32 URL-safe characters: allowed in Telegram start parameters (max 64)
        profile.telegram_link_token = secrets.token_urlsafe(24)
        profile.save(update_fields=['telegram_link_token'])
    return f'https://t.me/{username}?start={profile.telegram_link_token}'
