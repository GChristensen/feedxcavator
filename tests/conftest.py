"""Test setup: an isolated home directory with its own database and workspace."""

import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_HOME = Path(tempfile.mkdtemp(prefix="feedxcavator-test-"))
os.environ["FEEDXCAVATOR_HOME"] = str(_HOME)
os.environ["DJANGO_SETTINGS_MODULE"] = "feedxcavator.settings"
os.environ["FEEDXCAVATOR_NO_AUTOLOAD"] = "1"
(_HOME / "config.yml").write_text(
    "hostname: 'http://feeds.test'\nwebsub-hostname: 'http://feeds.test'\nauthentication: false\nuser: user\n",
    encoding="utf-8",
)

import django  # noqa: E402

django.setup()

from django.core.management import call_command  # noqa: E402

call_command("migrate", interactive=False, verbosity=0)


def pytest_sessionfinish(session, exitstatus):
    from django.db import connections

    connections.close_all()
    shutil.rmtree(_HOME, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean():
    """Every test starts with empty tables, an empty workspace and no loaded registry."""
    from django.apps import apps

    from feedxcavator import conf, context, fetch, workspace

    for model in apps.get_app_config("feedxcavator").get_models():
        model.objects.all().delete()
    root = conf.workspace_dir()
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(conf.FILES_DIR, ignore_errors=True)
    root.mkdir(parents=True)
    workspace._live = workspace.Registry()
    workspace._last_signature = None
    workspace._reported = None
    fetch._host_sessions.clear()
    context.new_fetch_state()
    yield
    from django.db import connections

    connections.close_all()


@pytest.fixture
def ws():
    """Writes workspace files and loads them: ws({"feeds/a/feed.yaml": "..."}) returns the registry."""
    from feedxcavator import conf, workspace

    def write(files: dict, load=True, startup=False):
        for name, content in files.items():
            path = conf.workspace_dir() / name
            if content is None:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return workspace.load(startup=startup, force=True) if load else None

    write.root = conf.workspace_dir()
    return write


class _Handler(BaseHTTPRequestHandler):
    def _serve(self):
        server = self.server
        server.requests.append((self.command, self.path, dict(self.headers), self._body()))
        route = server.routes.get(self.path.split("?")[0])
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        if callable(route):
            route = route(self)
        status, headers, body = route
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    do_GET = do_POST = _serve

    def log_message(self, *args):
        pass


@pytest.fixture
def http():
    """A local HTTP server. http.routes[path] = (status, headers, body) or a function of the handler."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.routes = {}
    server.requests = []
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def html(body: str, status=200):
    return status, {"Content-Type": "text/html; charset=utf-8"}, body
