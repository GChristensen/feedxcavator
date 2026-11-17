"""Public and utility routes (API2-2). Blocking work runs in threads so the event loop stays free (PRC-5)."""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import hmac
import inspect
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.http import HttpResponse, HttpResponseRedirect, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.utils.http import http_date
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from feedxcavator import VERSION, conf, context, extraction, fetch, filters, reply, storage, websub, workspace
from feedxcavator.middleware import auth_enabled

STATIC_DIR = Path(__file__).resolve().parent / "static"
CHUNK = 64 * 1024


def threaded(fn):
    """Runs a synchronous view in a worker thread of its own and releases its database connection."""

    def call(*args, **kwargs):
        from django.db import connections

        try:
            return fn(*args, **kwargs)
        finally:
            connections.close_all()

    runner = sync_to_async(call, thread_sensitive=False)

    @functools.wraps(fn)
    async def view(*args, **kwargs):
        return await runner(*args, **kwargs)

    return view


# authentication ---------------------------------------------------------------------------------


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authorized(request) -> bool:
    """AUTH-6: the x-feedxcavator-auth header must carry the API token."""
    from feedxcavator.models import ApiToken

    token = request.headers.get("x-feedxcavator-auth")
    if not token:
        return False
    stored = ApiToken.objects.order_by("-id").first()
    return bool(stored and hmac.compare_digest(stored.token_hash, token_hash(token)))


def _safe_next(target: str | None) -> str:
    return target if target and target.startswith("/") and not target.startswith("//") else "/"


@threaded
def login(request):
    """AUTH-2."""
    if not auth_enabled():
        return HttpResponseRedirect("/console")
    next_url = _safe_next(request.GET.get("next") or request.POST.get("next"))
    failed = False
    if request.method == "POST":
        user = authenticate(request, username=request.POST.get("username"), password=request.POST.get("password"))
        if user is not None:
            auth_login(request, user)
            return HttpResponseRedirect(next_url)
        failed = True
    return render(request, "login.html", {"next": next_url, "failed": failed}, status=401 if failed else 200)


@threaded
def logout(request):
    auth_logout(request)
    return HttpResponseRedirect("/login")


async def home(request):
    return HttpResponseRedirect("/console")


@ensure_csrf_cookie
@threaded
def console(request):
    get_token(request)  # the console sends it back as X-CSRFToken
    index = STATIC_DIR / "console" / "index.html"
    if not index.exists():
        return HttpResponse(
            "<h2>The console is not built</h2><p>Run <code>npm install &amp;&amp; npm run build</code> in "
            "<code>frontend/</code>.</p>", status=503)
    response = HttpResponse(index.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response


# files ------------------------------------------------------------------------------------------


def parse_byte_range(header: str | None, length: int):
    """IMG-2: a single range as (start, end) inclusive, "unsatisfiable", or None for the whole file."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m:
        return None
    first, last = (int(g) if g else None for g in m.groups())
    if first is None and last is None:
        return None
    if first is None:  # the last N bytes
        return (max(0, length - last), length - 1) if last > 0 and length > 0 else "unsatisfiable"
    if first >= length or (last is not None and last < first):
        return "unsatisfiable"
    return first, min(last if last is not None else length - 1, length - 1)


async def _file_chunks(path: Path, start: int, length: int):
    f = await asyncio.to_thread(open, path, "rb")
    try:
        await asyncio.to_thread(f.seek, start)
        remaining = length
        while remaining > 0:
            chunk = await asyncio.to_thread(f.read, min(CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(f.close)


def file_response(request, path: Path, content_type: str | None, cache_control: str):
    """Streams a file from disk with range, ETag and If-Range support (IMG-2)."""
    stat = path.stat()
    length = stat.st_size
    etag = f'"{int(stat.st_mtime * 1000):x}-{length:x}"'
    last_modified = http_date(stat.st_mtime)
    headers = {
        "Content-Type": content_type or "application/octet-stream",
        "Accept-Ranges": "bytes",
        "Cache-Control": cache_control,
        "ETag": etag,
        "Last-Modified": last_modified,
    }
    if_range = request.headers.get("if-range")
    byte_range = None
    if if_range is None or if_range in (etag, last_modified):
        byte_range = parse_byte_range(request.headers.get("range"), length)
    if byte_range == "unsatisfiable":
        return HttpResponse(status=416, headers={**headers, "Content-Range": f"bytes */{length}"})
    if byte_range:
        start, end = byte_range
        part = end - start + 1
        response = StreamingHttpResponse(_file_chunks(path, start, part), status=206, headers=headers)
        response["Content-Range"] = f"bytes {start}-{end}/{length}"
        response["Content-Length"] = str(part)
        return response
    if request.method == "HEAD":
        return HttpResponse(headers={**headers, "Content-Length": str(length)})
    response = StreamingHttpResponse(_file_chunks(path, 0, length), headers=headers)
    response["Content-Length"] = str(length)
    return response


@csrf_exempt
async def image(request, blob_id):
    """API-27, API2-4."""
    blob = await sync_to_async(_find_blob, thread_sensitive=False)(blob_id)
    if blob is None:
        return reply.page_not_found()
    path = storage.blob_path(blob.sha256)
    if not path.exists():
        return reply.page_not_found()
    content_type = blob.content_type or "application/octet-stream"
    if conf.get("x-accel"):  # nginx serves the file and handles ranges, ETags and streaming
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = f"{str(conf.get('x-accel')).rstrip('/')}/{blob.sha256}"
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
    return file_response(request, path, content_type, "public, max-age=31536000, immutable")


def _find_blob(blob_id):
    from django.db import connections

    try:
        return storage.find_blob(blob_id)
    finally:
        connections.close_all()


async def static(request, path):
    root = STATIC_DIR.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return reply.page_not_found()
    hashed = re.search(r"[-.][0-9A-Za-z_-]{8,}\.\w+$", target.name) is not None
    cache = "public, max-age=31536000, immutable" if hashed else "no-cache"
    return file_response(request, target, mimetypes.guess_type(target.name)[0], cache)


async def favicon(request):
    return await static(request, "console/favicon.ico")


@csrf_exempt
@threaded
def save_image(request):
    """API-28 with AUTH2-4: stores the raw body as an image and returns its id."""
    if request.method != "POST":
        return HttpResponse(status=405)
    if not authorized(request):
        return reply.permission_denied()
    blob = storage.store_blob(request.body, request.headers.get("content-type") or "application/octet-stream")
    return reply.text_page(blob.id)


# feeds --------------------------------------------------------------------------------------------


def _stored(info):
    stored = storage.fetch_feed_output(info.key) if info.key else None
    return reply.web_page(stored["content_type"], stored["output"]) if stored else None


@csrf_exempt
@threaded
def feed(request, suffix):
    """EX-11, UC2-8, and the resolution of section 17.2: a feed with tasks is served from storage."""
    registry = workspace.get_registry()
    info = registry.info_by_id(suffix[5:]) if suffix.startswith("uuid:") else registry.info_by_suffix(suffix)
    if info is None or (info.status == "ok" and not info.enabled):
        return reply.page_not_found()
    if info.status != "ok" or info.feed is None:  # a feed that failed to load
        return _stored(info) or reply.service_unavailable(f"<h2>The feed failed to load</h2><pre>{info.error}</pre>")
    if info.tasks:
        return _stored(info) or reply.no_content()
    with context.scope(source=extraction.extractor_name(info.feed)):
        result = extraction.run_feed(info.feed, "request", registry)
    if result.output is None:
        return reply.no_content()
    return reply.web_page(result.output["content_type"], result.output["output"])


class HandlerRequest:
    """The request given to a handler declared with the single parameter `request`."""

    def __init__(self, request):
        self.django = request
        self.method = request.method
        self.path = request.path
        self.headers = {k.lower(): v for k, v in request.headers.items()}
        self.body = request.body  # read before the form data, which consumes the stream
        self.params = {**request.GET.dict(), **request.POST.dict()}

    @property
    def json(self):
        return json.loads(self.body or b"null")

    def __getitem__(self, key):
        return getattr(self, str(getattr(key, "name", key)).replace("-", "_"))

    def get(self, key, default=None):
        try:
            return self[key]
        except AttributeError:
            return default


def _as_response(value):
    if isinstance(value, HttpResponse) or hasattr(value, "streaming_content"):
        return value
    if value is None:
        return reply.no_content()
    if isinstance(value, (dict, list)):
        return reply.json_page(value)
    return reply.html_page(str(value))


@csrf_exempt
@threaded
def handler(request, name):
    """HND-1 to HND-5, UC2-10."""
    registry = workspace.get_registry()
    registration = registry.handlers.get(name)
    if registration is None:
        if name in registry.failed_handlers:
            return reply.service_unavailable("<h2>The handler failed to load</h2>")
        return reply.page_not_found()
    if registration.meta.get("auth") and not authorized(request):
        return reply.permission_denied()
    wrapped = HandlerRequest(request)
    parameters = list(inspect.signature(registration.fn).parameters)
    with context.scope(source=name):
        if parameters == ["request"]:
            return _as_response(registration.fn(wrapped))
        return _as_response(registration.fn(*[wrapped.params.get(p, wrapped.params.get(p.replace("_", "-")))
                                              for p in parameters]))


# redirects and forwards -------------------------------------------------------------------------------


def _raw_tail(request, prefix_parts: int) -> str:
    """The rest of the raw path after `prefix_parts` segments, still percent-encoded (CFG-6)."""
    raw = request.scope.get("raw_path") if hasattr(request, "scope") else None
    path = raw.decode("latin-1") if raw else request.get_full_path().split("?")[0]
    return path.lstrip("/").split("/", prefix_parts)[-1]


_SCHEME_RE = re.compile(r"([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_SCRIPT_SCHEMES = {"javascript", "vbscript", "data"}


def _redirect_target(target):
    """A 302 to `target` when it is an absolute URL of any scheme (feeds link to custom ones such as
    `ext+jb://`, which Ring passed through), except script-bearing schemes."""
    match = _SCHEME_RE.match(target)
    if not match or match.group(1).lower() in _SCRIPT_SCHEMES:
        return reply.page_not_found()
    return reply.redirect_to(target)


@csrf_exempt
async def redirect(request, random, url):
    """API-30."""
    target = unquote(_raw_tail(request, 2))
    query = request.META.get("QUERY_STRING")
    if query and "?" not in target:  # an unencoded target: the server split its query off
        base, hash_, fragment = target.partition("#")
        target = f"{base}?{query}{hash_}{fragment}"
    return _redirect_target(target)


@csrf_exempt
async def redirect_b64(request, random, data):
    """API-31."""
    try:
        target = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return reply.page_not_found()
    return _redirect_target(target)


_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
                "transfer-encoding", "upgrade", "content-encoding", "content-length", "set-cookie", "server",
                "x-frame-options", "content-security-policy", "strict-transport-security"}


def _forward(request, url, referer, proxy):
    headers = {"Referer": referer} if referer else {}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    response = fetch.fetch_url(url, headers=headers, proxy=proxy or None, retry=False)
    if response is None:
        status = fetch.get_last_http_error()
        return HttpResponse(f"Upstream error: {status or fetch.get_last_network_error()}", status=502,
                            content_type="text/plain")
    out = HttpResponse(response.body, status=206 if response.status == 206 else 200)
    del out["Content-Type"]
    for name, value in response.headers.items():
        if name not in _HOP_HEADERS:
            out[name] = value
    return out


@csrf_exempt
@threaded
def forward(request, random=None):
    """API-32."""
    url = request.GET.get("url")
    if not url:
        return reply.page_not_found()
    return _forward(request, url, request.GET.get("referer"), request.GET.get("proxy"))


@csrf_exempt
@threaded
def forward_b64(request, data):
    """API-33: `data` is URL-safe Base64 of `<url>;;<referer>`."""
    try:
        decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return reply.page_not_found()
    url, _, referer = decoded.partition(";;")
    return _forward(request, url, referer, request.GET.get("proxy"))


# websub, backend, word filters -------------------------------------------------------------------------


@csrf_exempt
@threaded
def websub_hub(request):
    """API-34. GET answers 405 (fixes section 17.8)."""
    if request.method != "POST":
        return HttpResponse(status=405, headers={"Allow": "POST"})
    return HttpResponse(status=websub.hub_action(request.POST.dict()))


@threaded
def run_task(request, task):
    """TSK-8."""
    from feedxcavator import worker

    try:
        worker.enqueue_task(task, "manual")
        return reply.text_page(f"{task} enqueued")
    except Exception:  # noqa: BLE001
        return reply.text_page("ERROR")


@threaded
def reschedule(request):
    """SCH-7: triggers a reload of schedules.yaml."""
    from feedxcavator import worker

    workspace.load(force=True)
    worker.enqueue("reschedule")
    return reply.text_page("OK")


@threaded
def service_task(request):
    """API-37."""
    from feedxcavator import worker

    worker.enqueue("service-task")
    return reply.text_page("OK")


def _json_body(request) -> dict:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@csrf_exempt
@threaded
def wordfilter(request, action):
    """API-40 to API-43."""
    if request.method != "POST":
        return HttpResponse(status=405)
    if not authorized(request):
        return reply.permission_denied()
    body = _json_body(request)
    name = body.get("word-filter") or "default"
    if action == "list":
        return reply.json_page(filters.list_word_filters())
    if action == "list-words":
        words = filters.list_word_filter(name)
        return reply.json_page(words) if words else reply.page_not_found()
    if action in ("add-regex", "remove-regex"):
        if not body.get("regex"):
            return reply.page_not_found()
        try:
            (filters.add_filter_regex if action == "add-regex" else filters.remove_filter_regex)(name, body["regex"])
        except re.error as e:
            return HttpResponse(f"Invalid regular expression: {e}", status=400, content_type="text/plain")
        return reply.no_content()
    if action in ("add-word", "remove-word"):
        if not body.get("word"):
            return reply.page_not_found()
        (filters.add_filter_word if action == "add-word" else filters.remove_filter_word)(name, body["word"])
        return reply.no_content()
    return reply.page_not_found()


def not_found(request, exception=None):
    """API-38. Django calls error handlers synchronously."""
    return reply.page_not_found()


def version() -> str:
    return VERSION
