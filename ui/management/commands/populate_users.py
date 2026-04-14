import string
import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from ui.models import UserProfile

class Command(BaseCommand):
    help = 'Populate the database with 50 random user accounts'

    def handle(self, *args, **kwargs):
        self.stdout.write('Starting population of 50 users...')
        
        # Helper to generate random string
        def random_string(length=10):
            letters = string.ascii_letters + string.digits
            return ''.join(random.choice(letters) for _ in range(length))

        # Delete existing users to avoid duplicates during testing
        User.objects.filter(username__startswith='student').delete()
        
        created_count = 0
        for i in range(1, 51):
            email = f'student{i}@example.com'
            password = 'bluebook2026'
            
            # Create user
            user = User.objects.create_user(
                username=email,
                email=email,
                password=password
            )
            
            # Create associated user profile (leaving fields blank)
            UserProfile.objects.create(
                user=user,
                full_name='',
                test_center_address='',
                contact_email=''
            )
            created_count += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully created {created_count} random user accounts.'))
        self.stdout.write(self.style.SUCCESS('You can now log in using credentials like:'))
        self.stdout.write(self.style.SUCCESS('Email: student1@example.com'))
        self.stdout.write(self.style.SUCCESS('Password: bluebook2026'))
