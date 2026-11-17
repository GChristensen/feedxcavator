"""Response builders for handlers (UC-7)."""

from __future__ import annotations

import json

from django.http import HttpResponse
from django.utils.encoding import iri_to_uri


def web_page(content_type, body):
    """A 200 response with the given content type."""
    response = HttpResponse(body if body is not None else "", content_type=content_type)
    response["Cache-Control"] = "no-cache"
    return response


def text_page(body):
    """A plain text response."""
    return web_page("text/plain; charset=utf-8", body)


def html_page(body):
    """An HTML response."""
    return web_page("text/html; charset=utf-8", body)


def json_page(body):
    """A JSON response. Accepts JSON text or any JSON-serializable value."""
    if not isinstance(body, (str, bytes)):
        body = json.dumps(body, ensure_ascii=False, default=str)
    return web_page("application/json", body)


def rss_page(body):
    """An RSS response."""
    return web_page("application/rss+xml; charset=utf-8", body)


def attachment_page(filename, body):
    """A response the browser saves as a file."""
    response = HttpResponse(body if body is not None else "", content_type="application/octet-stream")
    response["Content-Disposition"] = f"attachment; filename={filename}"
    response["Cache-Control"] = "no-cache"
    return response


def redirect_to(location):
    """A 302 redirect."""
    # Like Ring's redirect, any Location is sent as is. Django's HttpResponseRedirect refuses schemes other than
    # http, https and ftp, which breaks links to custom schemes (ext+jb://...) that feeds use.
    response = HttpResponse(status=302)
    response["Location"] = iri_to_uri(str(location))
    return response


def no_content():
    """A 204 response."""
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-cache"
    return response


def permission_denied():
    """A 403 response."""
    return HttpResponse("<h2>Permission denied</h2>", status=403, content_type="text/html")


def page_not_found():
    """A 404 response."""
    return HttpResponse("<h2>Page not found</h2>", status=404, content_type="text/html")


def internal_server_error(message=None):
    """A 500 response."""
    return HttpResponse(message or "<h2>Internal server error</h2>", status=500, content_type="text/html")


def service_unavailable(message=None):
    """A 503 response."""
    return HttpResponse(message or "<h2>Service unavailable</h2>", status=503, content_type="text/html")
