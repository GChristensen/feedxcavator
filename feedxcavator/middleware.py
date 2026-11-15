"""Request context, access control (AUTH-5) and CSRF for the console API (AUTH2-3)."""

from __future__ import annotations

import traceback
from urllib.parse import quote

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware

from feedxcavator import conf, context

PUBLIC_PREFIXES = ("/api/", "/image/", "/feed/", "/handler/", "/redirect/", "/redirect-b64/", "/forward/",
                   "/forward-b64/", "/websub", "/static/", "/favicon.ico")
PUBLIC_PATHS = ("/", "/login", "/logout")
CONSOLE_API = "/front/api/"


def auth_enabled() -> bool:
    return conf.get("authentication", True) is not False


class _AsyncMiddleware:
    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)


class RequestContextMiddleware(_AsyncMiddleware):
    """Sets the per-request ambient state and turns unhandled errors into responses."""

    async def __call__(self, request):
        context.new_fetch_state()
        token = context.request_host.set(f"{request.scheme}://{request.get_host()}")
        try:
            response = await self.get_response(request)
        finally:
            context.request_host.reset(token)
        return response

    def process_exception(self, request, exception):
        from feedxcavator import applog

        try:
            applog.write("error", exception)
        except Exception:  # noqa: BLE001, S110
            pass
        if request.path.startswith(CONSOLE_API):
            body = {"error": f"{type(exception).__name__}: {exception}"}
            if conf.get("stacktraces", True):
                body["traceback"] = traceback.format_exc()
            return JsonResponse(body, status=500)
        bare = not conf.get("stacktraces", True) or (conf.get("production") and request.path.startswith("/feed/"))
        if bare:
            return HttpResponse("<h2>Internal server error</h2>", status=500, content_type="text/html")
        return HttpResponse(traceback.format_exc(), status=500, content_type="text/plain; charset=utf-8")


class _CsrfCheck(CsrfViewMiddleware):
    def _reject(self, request, reason):
        return HttpResponseForbidden(f"CSRF verification failed: {reason}")


def _checked_view(request):  # a stand-in callback without csrf_exempt
    return None


class AccessMiddleware(_AsyncMiddleware):
    async def __call__(self, request):
        path = request.path
        public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
        if auth_enabled() and not public:
            user = await request.auser()
            if not user.is_authenticated:
                if path.startswith(CONSOLE_API):
                    return JsonResponse({"error": "authentication required"}, status=401)
                return HttpResponseRedirect(f"/login?next={quote(request.get_full_path())}")
        if path.startswith(CONSOLE_API) and request.method not in ("GET", "HEAD", "OPTIONS"):
            rejection = _CsrfCheck(lambda r: None).process_view(request, _checked_view, (), {})
            if rejection is not None:
                return rejection
        response = await self.get_response(request)
        return response
