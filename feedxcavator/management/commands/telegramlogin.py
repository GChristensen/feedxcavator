"""TG-6: the interactive Telegram login."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Logs in to Telegram (phone number and code) and creates the session under data/telegram/."

    def handle(self, *args, **options):
        from feedxcavator import telegram

        telegram.login()
