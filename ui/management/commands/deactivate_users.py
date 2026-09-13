from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Deactivates all users except staff members'

    def handle(self, *args, **options):
        User = get_user_model()
        count = User.objects.filter(is_staff=False).update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f"Successfully deactivated {count} users."))
