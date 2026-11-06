from django.core.management.base import BaseCommand

from ui.models import SiteSettings


class Command(BaseCommand):
    help = (
        'Turns skipping modules and breaks (by clicking the timer) on or off for '
        'students, or shows the current state. Staff can always skip.'
    )

    def add_arguments(self, parser):
        parser.add_argument('state', nargs='?', choices=['on', 'off'],
                            help='Leave out to show the current state')

    def handle(self, *args, **options):
        state = options['state']
        if state:
            SiteSettings.objects.update_or_create(pk=1, defaults={'allow_skipping': state == 'on'})
        current = 'ON' if SiteSettings.skipping_enabled() else 'OFF'
        self.stdout.write(self.style.SUCCESS(f"Skipping for students is {current}. Staff can always skip."))
        if state:
            self.stdout.write("Tests already open keep their old setting until the page is reloaded.")
