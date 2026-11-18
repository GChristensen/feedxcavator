"""Sets the password of the single user."""

import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from feedxcavator import conf


class Command(BaseCommand):
    help = "Creates the console user (or updates it) and sets its password."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="login name; defaults to `user` in config.yml")
        parser.add_argument("--password", help="the password; asked for when omitted")

    def handle(self, *args, **options):
        username = options["user"] or conf.get("user")
        if not username:
            raise CommandError("no user name: pass --user or set `user` in config.yml")
        password = options["password"]
        if not password:
            password = getpass.getpass(f"New password for {username}: ")
            if password != getpass.getpass("Repeat the password: "):
                raise CommandError("the passwords do not match")
        if not password:
            raise CommandError("the password must not be empty")
        User = get_user_model()
        User.objects.exclude(username=username).delete()  # there is one user
        user, _ = User.objects.get_or_create(username=username)
        user.set_password(password)
        user.is_staff = user.is_superuser = True
        user.save()
        self.stdout.write(f"Password set for {username}.")
