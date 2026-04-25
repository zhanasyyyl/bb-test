from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    test_center_address = models.TextField(blank=True, null=True)
    contact_email = models.EmailField(blank=True, null=True)

    @property
    def first_name(self):
        if self.full_name:
            return self.full_name.split()[0]
        return ""

    def __str__(self):
        return self.user.username
