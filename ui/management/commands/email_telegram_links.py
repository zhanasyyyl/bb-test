"""One-off: emails each stored contact address a link to connect Telegram.

Messages now go to Telegram, so this tells the people who used to get start
codes by email how to keep getting them. It's the only email the site sends,
through Resend's API directly (RESEND_API_KEY in .env).

    python manage.py email_telegram_links           # dry run: shows who'd get what
    python manage.py email_telegram_links --send    # sends
"""

import html
import json
import os
import time
import urllib.error
import urllib.request
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from ui import telegram
from ui.models import UserProfile

RESEND_URL = 'https://api.resend.com/emails'
FROM = 'BB Test <messages@bbtest.shop>'
SUBJECT = 'Connect Telegram to keep getting BB Test start codes'
# Resend allows 20 requests/s for this account; stay well under it
SEND_INTERVAL = 0.1


class Command(BaseCommand):
    help = 'Emails each stored contact address a link to connect Telegram (dry run unless --send)'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true', help='Actually send (default: dry run)')
        parser.add_argument('--include-inactive', action='store_true', help='Also email contacts of inactive users')

    def handle(self, *args, send=False, include_inactive=False, **options):
        api_key = os.getenv('RESEND_API_KEY', '')
        if send and not api_key:
            raise CommandError('Set RESEND_API_KEY in .env to send.')

        recipients, skipped = self.collect(include_inactive)
        students = sum(len(v) for v in recipients.values())
        self.stdout.write(f"{len(recipients)} addresses for {students} students. Skipped: " +
                          ', '.join(f'{n} {why}' for why, n in skipped.items() if n) + '.')
        for address, entries in list(recipients.items())[:5]:
            self.stdout.write(f"  {address}: {', '.join(name for name, _ in entries)}")
        if len(recipients) > 5:
            self.stdout.write(f'  … and {len(recipients) - 5} more')
        if not send:
            self.stdout.write('Dry run: nothing sent. Add --send to send.')
            return

        sent, failed = 0, []
        for address, entries in recipients.items():
            try:
                self.send_email(api_key, address, entries)
                sent += 1
            except Exception as e:
                failed.append((address, e))
            time.sleep(SEND_INTERVAL)
        self.stdout.write(self.style.SUCCESS(f'Sent {sent} emails.'))
        for address, e in failed:
            self.stdout.write(self.style.ERROR(f'  Failed: {address}: {e}'))

    def collect(self, include_inactive):
        """{address: [(student name, connect link), ...]} plus skip counts."""
        recipients = defaultdict(list)
        skipped = {'already connected': 0, 'inactive': 0, 'without a contact email': 0}
        profiles = UserProfile.objects.select_related('user').order_by('user__date_joined')
        for profile in profiles:
            address = (profile.contact_email or '').strip().lower()
            if not address:
                skipped['without a contact email'] += 1
            elif profile.telegram_chat_id:
                skipped['already connected'] += 1
            elif not profile.user.is_active and not include_inactive:
                skipped['inactive'] += 1
            else:
                link = telegram.connect_link(profile)
                if link is None:
                    raise CommandError('Set TELEGRAM_BOT_USERNAME in .env first.')
                name = ' '.join((profile.full_name or profile.user.get_username()).split())
                recipients[address].append((name, link))
        return recipients, skipped

    def send_email(self, api_key, address, entries):
        lines = [f'{name}: {link}' for name, link in entries]
        text = ('BB Test now sends start codes, unscheduled break notices and notes through Telegram instead of email.\n\n'
                f"To keep getting them, open the link below{' for each student' if len(entries) > 1 else ''} "
                'on a device with Telegram and press Start:\n\n' + '\n'.join(lines) +
                "\n\nYou're receiving this because this address was the contact email on a BB Test account.\n"
                'BB Test is an independent SAT practice site. https://bbtest.shop\n')
        items = ''.join(f'<li style="margin-bottom: 8px;"><strong>{html.escape(name)}</strong>: '
                        f'<a href="{html.escape(link)}">Connect Telegram</a></li>' for name, link in entries)
        body = ('<div style="font-family: Arial, Helvetica, sans-serif; font-size: 15px; line-height: 22px; color: #1e1e1e; max-width: 480px;">'
                '<p><strong>BB Test</strong></p>'
                '<p>BB Test now sends start codes, unscheduled break notices and notes through Telegram instead of email.</p>'
                f"<p>To keep getting them, open the link{' for each student' if len(entries) > 1 else ''} "
                'on a device with Telegram and press <strong>Start</strong>:</p>'
                f'<ul style="padding-left: 20px;">{items}</ul>'
                '<p style="font-size: 12px; color: #666666;">You\'re receiving this because this address was the contact '
                'email on a BB Test account. BB Test is an independent SAT practice site &middot; '
                '<a href="https://bbtest.shop" style="color: #666666;">bbtest.shop</a></p></div>')
        payload = json.dumps({'from': FROM, 'to': [address], 'subject': SUBJECT, 'text': text, 'html': body}).encode()
        for attempt in range(3):
            request = urllib.request.Request(RESEND_URL, data=payload, headers={
                'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json',
                'User-Agent': 'bbtest-email-telegram-links'})
            try:
                with urllib.request.urlopen(request, timeout=15):
                    return
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:      # rate limited: wait and retry
                    time.sleep(2)
                    continue
                detail = e.read().decode(errors='replace')[:200]
                raise RuntimeError(f'Resend returned HTTP {e.code}: {detail}') from None
