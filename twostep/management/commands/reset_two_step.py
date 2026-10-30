from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from twostep.models import TwoStep


class Command(BaseCommand):
    help = ("Switch two-step login off for one staff login, for a lost phone with no backup codes left. "
            "The next Admin sign-in takes the password alone and goes straight to setting up a new app.")

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username, **options):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No login called {username}.")
        deleted, _ = TwoStep.objects.filter(user=user).delete()
        self.stdout.write(f"Two-step login reset for {username}." if deleted
                          else f"{username} had no two-step login set up.")
