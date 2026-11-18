"""HTTP fetching on curl_cffi."""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, unquote, urlsplit

from feedxcavator import applog, conf, context

logger = logging.getLogger("feedxcavator.fetch")

DEFAULT_RETRIES = 2
RETRY_BASE_DELAY_MS = 100
RETRY_MAX_AFTER_MS = 10000  # a server asking to wait longer than this is not retried
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = 30.0
DEFAULT_IMPERSONATE = "chrome"

# an MTProto proxy (a Telegram proxy, `secret` instead of user and password) serves only Telegram calls
MTPROTO = "mtproto"

# name -> {host, port, user, password, scheme, secret}; replaced by the workspace loader on every load
proxy_resolver = lambda name: None  # noqa: E731

# host -> {cookies, user_agent, impersonate}: sessions handed over by the browser
_host_sessions: dict[str, dict] = {}
_host_lock = threading.Lock()
_sleep = time.sleep
_random = random.random


class Response:
    """A raw HTTP response. Fields are available as attributes and by key (`final_url`, `content_type`)."""

    def __init__(self, status, headers, body, url, final_url=None):
        self.status = status
        self.headers = {str(k).lower(): v for k, v in dict(headers or {}).items()}
        self.body = body or b""
        self.url = url
        self.final_url = final_url or url
        self.content_type = self.headers.get("content-type")
        self._charset = None

    def header(self, name: str):
        return self.headers.get(name.lower())

    @property
    def text(self) -> str:
        return decode_body(self.body, self.content_type, self._charset)

    def json(self):
        return json.loads(self.text)

    def __getitem__(self, key):
        try:
            return getattr(self, str(getattr(key, "name", key)).replace("-", "_"))
        except AttributeError:
            raise KeyError(key) from None

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __repr__(self):
        return f"<Response {self.status} {self.final_url}>"


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)""", re.I)
_XML_ENCODING = re.compile(rb"""<\?xml[^>]+encoding\s*=\s*["']([\w-]+)""", re.I)


def decode_body(body: bytes, content_type: str | None = None, charset: str | None = None) -> str:
    """Decodes with the given charset, else the one of the response, then of the document, then UTF-8."""
    candidates = []
    if charset and str(charset).lower() != "auto":
        candidates.append(str(charset))
    else:
        m = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type or "", re.I)
        if m:
            candidates.append(m.group(1))
        m = _META_CHARSET.search(body[:4096]) or _XML_ENCODING.search(body[:256])
        if m:
            candidates.append(m.group(1).decode("ascii", "replace"))
    candidates.append("utf-8")
    for name in candidates:
        try:
            return body.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def is_mtproto(definition: dict | None) -> bool:
    return bool(definition) and str(definition.get("scheme") or "").lower() == MTPROTO


def _proxy_link(value: str) -> dict | None:
    """A Telegram proxy link, `tg://proxy?server=...&port=...&secret=...` or its https://t.me/proxy form, as an
    MTProto proxy definition; None for any other URL."""
    parts = urlsplit(value)
    link = ((parts.scheme == "tg" and parts.netloc == "proxy")
            or (parts.scheme in ("http", "https") and parts.hostname in ("t.me", "telegram.me")
                and parts.path.rstrip("/") == "/proxy"))
    if not link:
        return None
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    port = query.get("port", "")
    return {"host": query.get("server"), "port": int(port) if port.isdigit() else None, "scheme": MTPROTO,
            "user": None, "password": None, "secret": query.get("secret")}


def _config_proxy(name: str) -> dict | None:
    """A proxy from the `proxies` key of config.yml: a mapping (host, port, user, password, scheme, secret), a URL
    such as `socks5://user:password@host:1080` or `mtproto://host:443?secret=...`, or a Telegram proxy link."""
    proxies = conf.get("proxies") or {}
    value = proxies.get(name) if isinstance(proxies, dict) else None
    if value is None:
        return None
    if isinstance(value, str):
        definition = _proxy_link(value)
        if definition is None:
            parts = urlsplit(value if "://" in value else f"http://{value}")
            try:
                port = parts.port
            except ValueError:
                port = None
            definition = {"host": parts.hostname, "port": port, "scheme": parts.scheme or "http",
                          "user": unquote(parts.username) if parts.username else None,
                          "password": unquote(parts.password) if parts.password else None,
                          "secret": (parse_qs(parts.query).get("secret") or [None])[0]}
    elif isinstance(value, dict):
        definition = {"host": value.get("host"), "port": value.get("port"), "scheme": value.get("scheme") or "http",
                      "user": value.get("user"), "password": value.get("password"), "secret": value.get("secret")}
    else:
        definition = {}
    if not definition.get("host") or not definition.get("port"):
        logger.warning("proxy '%s' in config.yml needs a host and a port", name)
        return None
    if is_mtproto(definition) and not definition.get("secret"):
        logger.warning("MTProto proxy '%s' in config.yml needs a secret", name)
        return None
    return definition


def resolve_proxy(name) -> dict | None:
    """The proxy definition for a name: one defined in workspace code, else one from config.yml."""
    if not name:
        return None
    name = str(getattr(name, "name", name))
    return proxy_resolver(name) or _config_proxy(name)


def proxy_url(proxy: dict) -> str:
    scheme = proxy.get("scheme") or proxy.get("type") or "http"
    auth = ""
    if proxy.get("user"):
        auth = quote(str(proxy["user"]), safe="")
        if proxy.get("password"):
            auth += ":" + quote(str(proxy["password"]), safe="")
        auth += "@"
    return f"{scheme}://{auth}{proxy['host']}:{proxy['port']}"


def _transport(request: dict) -> Response:
    """Performs one request with curl_cffi. Tests replace this function."""
    from curl_cffi import requests as curl

    kwargs = {
        "headers": request.get("headers") or None,
        "data": request.get("payload"),
        "cookies": request.get("cookies") or None,
        "timeout": request.get("timeout") or DEFAULT_TIMEOUT,
        "allow_redirects": request.get("follow_redirects", True),
        "verify": not request.get("insecure"),
        "impersonate": request.get("impersonate") or DEFAULT_IMPERSONATE,
    }
    if request.get("proxy"):
        kwargs["proxies"] = {"http": request["proxy"], "https": request["proxy"]}
    response = curl.request(str(request.get("method") or "GET").upper(), request["url"], **kwargs)
    return Response(response.status_code, dict(response.headers), response.content, request["url"], response.url)


def _retry_after_ms(response: Response) -> int | None:
    value = (response.header("retry-after") or "").strip()
    if not value:
        return None
    try:
        if value.isdigit():
            return int(value) * 1000
        return max(0, int((parsedate_to_datetime(value).timestamp() - time.time()) * 1000))
    except (TypeError, ValueError):
        return None


def _random_ms(n) -> int:
    return int(_random() * max(1, n))


def retry_delay_ms(backoff: int, requested: int | None) -> int:
    """Equal jitter: half of the backoff plus a random part of the other half. A requested delay
    is never shortened, only extended by up to 10%."""
    half = backoff // 2
    jittered = half + _random_ms(backoff - half)
    if requested is not None:
        return max(jittered, requested + _random_ms(min(backoff, requested // 10) + 1))
    return jittered


MIN_BUDGET = 1.0  # seconds: with less left of a request's time budget, no new attempt starts


def remaining_budget() -> float | None:
    """Seconds left of the time budget of a feed request (`request-timeout`), or None outside one."""
    deadline = context.deadline.get()
    return None if deadline is None else deadline - time.monotonic()


class BudgetExceeded(TimeoutError):
    """The time budget of a feed request (`request-timeout`) is used up."""


def _wait(ms: int):
    event = context.cancel_event.get()
    if event is not None:
        event.wait(ms / 1000)
        context.check_cancelled()
    else:
        _sleep(ms / 1000)


def adopt_browser_session(url: str, cookies: dict, user_agent: str | None, impersonate: str | None = None):
    """Later fetches for the host reuse the cookies and user agent of the browser."""
    host = urlsplit(url).hostname
    if host and cookies:
        with _host_lock:
            _host_sessions[host] = {"cookies": dict(cookies), "user_agent": user_agent, "impersonate": impersonate}


def _convert(response: Response, as_, charset):
    if not as_:
        response._charset = charset
        return response
    if as_ == "bytes":
        return response.body
    text = decode_body(response.body, response.content_type, charset)
    if as_ == "string":
        return text
    if as_ == "json":
        return json.loads(text)
    from parsel import Selector

    if as_ == "html":
        return Selector(text=text)
    if as_ == "xml":
        return Selector(text=text, type="xml")
    raise ValueError(f"unsupported response conversion '{as_}': expected html, xml, json, string or bytes")


_OPTION_ALIASES = {"as_": "as", "async_": "async", "async?": "async", "insecure?": "insecure",
                   "follow-redirects": "follow_redirects"}
_OPTIONS = {"method", "headers", "payload", "as", "charset", "timeout", "proxy", "follow_redirects", "retry",
            "async", "insecure", "impersonate", "cookies"}


def fetch_url(url, **options):
    """Fetches a URL. Never raises on network or HTTP errors: returns None and records the failure,
    readable through get_last_http_error() and the other accessors.

    Options: method, headers, payload, `as` (html, xml, json, string, bytes; without it the raw response
    is returned), charset, timeout (milliseconds), proxy (a proxy name, False for a direct connection; a name
    that is not defined fails the fetch), follow_redirects (with False a 3xx response is returned, its target
    in `response.header("location")`), retry (False or a number of retries, default 2), `async` (fire and forget),
    insecure (skip TLS checks), impersonate (curl_cffi browser profile), cookies.
    In Python pass `as` and `async` as `as_` and `async_`."""
    opts = {}
    for key, value in options.items():
        key = _OPTION_ALIASES.get(key, key)
        if key not in _OPTIONS:
            raise TypeError(f"fetch_url() got an unexpected option '{key}'")
        opts[key] = value
    state = context.fetch_state()
    state.reset()
    context.check_cancelled()

    feed = context.current_feed.get()
    feed_fetch = (feed.get("fetch") if feed is not None else None) or {}
    as_ = opts.get("as")
    as_ = str(getattr(as_, "name", as_)) if as_ else None
    if as_ == "edn":
        raise ValueError("EDN is not supported: use as='json'")
    charset = opts.get("charset") or feed_fetch.get("charset") or "auto"
    timeout = opts.get("timeout")
    timeout = (timeout / 1000) if timeout else (feed_fetch.get("timeout") or DEFAULT_TIMEOUT)
    retry = opts.get("retry")
    if retry is False or opts.get("async"):
        attempts = 1
    elif isinstance(retry, (int, float)) and not isinstance(retry, bool):
        attempts = 1 + max(0, int(retry))
    else:
        attempts = 1 + DEFAULT_RETRIES

    proxy = None
    proxy_name = opts.get("proxy")
    if proxy_name is not False:
        proxy_name = proxy_name or feed_fetch.get("proxy")
        if proxy_name:
            definition = resolve_proxy(proxy_name)
            if not definition:  # fail closed: a misspelt name must not mean a direct connection
                name = str(getattr(proxy_name, "name", proxy_name))
                state.network_error = LookupError(f"proxy '{name}' is not defined")
                applog.write("error", f"Fetch error\nURL: {url}\nError: {state.network_error}")
                return None
            if is_mtproto(definition):  # it cannot carry HTTP; connecting directly instead would leak the address
                name = str(getattr(proxy_name, "name", proxy_name))
                state.network_error = LookupError(f"proxy '{name}' is an MTProto proxy, usable only by Telegram")
                applog.write("error", f"Fetch error\nURL: {url}\nError: {state.network_error}")
                return None
            proxy = proxy_url(definition)

    headers = dict(opts.get("headers") or {})
    cookies = dict(opts.get("cookies") or {})
    impersonate = opts.get("impersonate") or feed_fetch.get("impersonate") or DEFAULT_IMPERSONATE
    host = urlsplit(str(url)).hostname
    handed_over = _host_sessions.get(host) if host else None
    if handed_over:
        cookies = {**handed_over["cookies"], **cookies}
        if handed_over.get("user_agent") and not any(k.lower() == "user-agent" for k in headers):
            headers["User-Agent"] = handed_over["user_agent"]
        impersonate = opts.get("impersonate") or handed_over.get("impersonate") or impersonate

    request = {
        "url": str(url),
        "method": str(getattr(opts.get("method"), "name", opts.get("method") or "GET")).upper(),
        "headers": headers,
        "payload": opts.get("payload"),
        "cookies": cookies,
        "timeout": timeout,
        "proxy": proxy,
        "follow_redirects": opts.get("follow_redirects", True) is not False,
        "insecure": bool(opts.get("insecure")),
        "impersonate": impersonate,
    }

    if opts.get("async"):
        threading.Thread(target=_fire, args=(request,), daemon=True, name="fetch-async").start()
        return None

    response, error = None, None
    for attempt in range(1, attempts + 1):
        remaining = remaining_budget()
        if remaining is not None:  # request mode: never run past `request-timeout`
            if remaining < MIN_BUDGET:
                if response is None and error is None:  # keep the failure of an earlier attempt, if any
                    error = BudgetExceeded("the request time budget is used up")
                break
            request["timeout"] = min(timeout, remaining)
        response, error = None, None
        try:
            response = _transport(request)
        except context.JobCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - any transport failure is a network error
            error = e
        if response is not None and response.status is not None and (
                response.status < 300 or (not request["follow_redirects"] and 300 <= response.status < 400)):
            try:
                return _convert(response, as_, charset)
            except ValueError as e:
                if "unsupported response conversion" in str(e):
                    raise
                state.conversion_error = e
                return None
            except Exception as e:  # noqa: BLE001
                state.conversion_error = e
                return None
        failed_retryably = error is not None or response.status in RETRYABLE_STATUSES
        requested = _retry_after_ms(response) if response is not None else None
        if not failed_retryably or attempt >= attempts or (requested is not None and requested > RETRY_MAX_AFTER_MS):
            break
        delay = retry_delay_ms(RETRY_BASE_DELAY_MS * 2 ** (attempt - 1), requested)
        remaining = remaining_budget()
        if remaining is not None and remaining - delay / 1000 < MIN_BUDGET:
            break  # the retry would not fit in the request's time budget
        _wait(delay)

    if error is not None:
        state.network_error = error
    else:
        state.response = response
        state.http_error = response.status
        if handed_over and response.status in (401, 403, 429, 503):
            with _host_lock:  # the handed-over session was rejected
                _host_sessions.pop(host, None)
    if context.log_fetch_errors.get():
        applog.write("error", f"Fetch error\nURL: {url}\nError: {state.http_error or error}")
    return None


def _fire(request):
    try:
        _transport(request)
    except Exception:  # noqa: BLE001 - async requests report nothing
        pass


def get_last_http_response():
    """The response of the last failed fetch_url call in this request or job."""
    return context.fetch_state().response


def get_last_http_error():
    """The HTTP status of the last failed fetch_url call, or None."""
    return context.fetch_state().http_error


def get_last_network_error():
    """The exception of the last fetch_url call that failed on the network level, or None."""
    return context.fetch_state().network_error


def get_last_conversion_error():
    """The error raised while converting the last response (`as`), or None."""
    return context.fetch_state().conversion_error


class log_fetch_errors:
    """A context manager: every failed fetch inside it writes an error entry to the application log."""

    def __enter__(self):
        self._token = context.log_fetch_errors.set(True)
        return self

    def __exit__(self, *exc):
        context.log_fetch_errors.reset(self._token)
        return False
