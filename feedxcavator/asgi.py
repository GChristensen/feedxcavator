"""The web process: the ASGI application."""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "feedxcavator.settings")
django.setup()

from django.core.asgi import get_asgi_application  # noqa: E402

from feedxcavator import applog, workspace  # noqa: E402

applog.install_excepthooks()
applog.install_stdout()
if not os.environ.get("FEEDXCAVATOR_NO_AUTOLOAD"):
    workspace.ensure_layout()
    workspace.load(startup=True)  # user code loads at startup
    workspace.start_watcher()

application = get_asgi_application()
