"""The interactive Telegram login."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Logs in to Telegram and creates the session under data/telegram/: by phone number and code, "
            "or with --qr by a QR code scanned in the Telegram app.")

    def add_arguments(self, parser):
        parser.add_argument("--qr", action="store_true",
                            help="show a QR code to scan in Telegram (Settings > Devices > Link Desktop Device)")

    def handle(self, *args, **options):
        from feedxcavator import telegram

        telegram.login(qr=options["qr"])
