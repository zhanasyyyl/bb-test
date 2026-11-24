import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from ui import telegram


class Command(BaseCommand):
    help = "Points the Telegram bot's webhook at this site and checks the bot settings"

    def add_arguments(self, parser):
        parser.add_argument('--url', help='Webhook URL (default: https://<first ALLOWED_HOSTS entry>/telegram/webhook/)')
        parser.add_argument('--if-configured', action='store_true',
                            help="Do nothing if TELEGRAM_BOT_TOKEN isn't set (for deploy scripts)")

    def handle(self, *args, url=None, if_configured=False, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            if if_configured:
                self.stdout.write("TELEGRAM_BOT_TOKEN isn't set; skipping Telegram setup.")
                return
            raise CommandError('Set TELEGRAM_BOT_TOKEN in .env (from @BotFather).')
        secret = settings.TELEGRAM_WEBHOOK_SECRET
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', secret):
            raise CommandError('Set TELEGRAM_WEBHOOK_SECRET in .env: 16 or more letters, digits, _ or -.')

        try:
            bot = telegram.call('getMe', {})
            if not url:
                host = next((h for h in settings.ALLOWED_HOSTS
                             if h not in ('localhost', '127.0.0.1') and not h.startswith('.') and h != '*'),
                            'bbtest.shop')
                url = f'https://{host}{reverse("telegram_webhook")}'
            telegram.call('setWebhook', {'url': url, 'secret_token': secret, 'allowed_updates': ['message']})
            info = telegram.call('getWebhookInfo', {})
        except telegram.TelegramError as e:
            raise CommandError(f'Telegram said: {e}') from None

        self.stdout.write(self.style.SUCCESS(f"Webhook for @{bot['username']} set to {info.get('url')}"))
        if settings.TELEGRAM_BOT_USERNAME.lstrip('@') != bot['username']:
            self.stdout.write(self.style.WARNING(
                f"TELEGRAM_BOT_USERNAME is '{settings.TELEGRAM_BOT_USERNAME}', but the bot is "
                f"@{bot['username']}; connect links won't work until they match."))
        if info.get('last_error_message'):
            self.stdout.write(self.style.WARNING(f"Telegram's last delivery error: {info['last_error_message']}"))
