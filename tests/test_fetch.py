import time
from email.utils import formatdate

import pytest

from feedxcavator import applog, context, fetch
from feedxcavator.feeddef import Feed
from feedxcavator.models import LogEntry


@pytest.fixture
def transport(monkeypatch):
    """Replaces the network: transport.responses is a list of Response objects or exceptions."""
    calls, sleeps = [], []

    def fake(request):
        calls.append(request)
        item = fake.responses.pop(0) if len(fake.responses) > 1 else fake.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    fake.responses = []
    fake.calls = calls
    fake.sleeps = sleeps
    monkeypatch.setattr(fetch, "_transport", fake)
    monkeypatch.setattr(fetch, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(fetch, "_random", lambda: 0.5)
    return fake


def response(status=200, body=b"ok", headers=None, url="http://t.test/"):
    return fetch.Response(status, headers or {"Content-Type": "text/html; charset=utf-8"}, body, url)


def test_success_and_conversions(transport):
    transport.responses = [response(body=b'{"a": [1, 2]}')]
    assert fetch.fetch_url("http://t.test/", as_="json") == {"a": [1, 2]}
    assert fetch.fetch_url("http://t.test/", **{"as": "string"}) == '{"a": [1, 2]}'
    assert fetch.fetch_url("http://t.test/", as_="bytes") == b'{"a": [1, 2]}'
    transport.responses = [response(body=b"<p class='x'>hi</p>")]
    assert fetch.fetch_url("http://t.test/", as_="html").css("p.x::text").get() == "hi"
    raw = fetch.fetch_url("http://t.test/")
    assert raw.status == 200 and raw["final-url"] == "http://t.test/" and raw.content_type.startswith("text/html")
    with pytest.raises(ValueError):
        fetch.fetch_url("http://t.test/", as_="edn")  # edn is not supported


def test_http_error_never_raises_and_records_state(transport):
    transport.responses = [response(404, b"nope")]
    assert fetch.fetch_url("http://t.test/missing") is None
    assert fetch.get_last_http_error() == 404
    assert fetch.get_last_http_response().body == b"nope"
    assert fetch.get_last_network_error() is None
    assert len(transport.calls) == 1  # 404 is not retried
    transport.responses = [response()]
    assert fetch.fetch_url("http://t.test/") is not None
    assert fetch.get_last_http_error() is None  # the state is reset at the start of every fetch


def test_retries_with_equal_jitter(transport):
    transport.responses = [response(503), response(503), response(200, b"fine")]
    assert fetch.fetch_url("http://t.test/", as_="string") == "fine"
    assert len(transport.calls) == 3
    assert transport.sleeps == [0.075, 0.15]  # 100 and 200 ms backoff: half plus half of the other half

    transport.calls.clear()
    transport.responses = [ConnectionError("down")]
    assert fetch.fetch_url("http://t.test/") is None
    assert len(transport.calls) == 3 and isinstance(fetch.get_last_network_error(), ConnectionError)

    transport.calls.clear()
    assert fetch.fetch_url("http://t.test/", retry=False) is None
    assert len(transport.calls) == 1
    transport.calls.clear()
    assert fetch.fetch_url("http://t.test/", retry=4) is None
    assert len(transport.calls) == 5


def test_retry_after(transport):
    transport.responses = [response(429, headers={"Retry-After": "2"}), response(200)]
    transport.sleeps.clear()
    assert fetch.fetch_url("http://t.test/") is not None
    assert 2.0 <= transport.sleeps[0] <= 2.2  # never shorter, extended by up to 10%

    transport.calls.clear()
    transport.responses = [response(429, headers={"Retry-After": "11"})]
    assert fetch.fetch_url("http://t.test/") is None
    assert len(transport.calls) == 1  # more than 10 seconds: gives up

    transport.calls.clear()
    transport.sleeps.clear()
    transport.responses = [response(503, headers={"Retry-After": formatdate(time.time() + 3, usegmt=True)}),
                           response(200)]
    assert fetch.fetch_url("http://t.test/") is not None
    assert 1.5 <= transport.sleeps[0] <= 3.5


def test_charset_auto_detection(transport):
    cyrillic = "Привет".encode("windows-1251")
    transport.responses = [response(body=cyrillic, headers={"Content-Type": "text/html; charset=windows-1251"})]
    assert fetch.fetch_url("http://t.test/", as_="string") == "Привет"  # the charset of the response
    transport.responses = [response(body=b'<meta charset="windows-1251">' + cyrillic,
                                    headers={"Content-Type": "text/html"})]
    assert fetch.fetch_url("http://t.test/", as_="string").endswith("Привет")  # from the document
    transport.responses = [response(body=cyrillic, headers={"Content-Type": "text/html; charset=utf-8"})]
    assert fetch.fetch_url("http://t.test/", as_="string", charset="windows-1251") == "Привет"  # explicit wins


def test_feed_defaults_and_proxy(transport, monkeypatch):
    monkeypatch.setattr(fetch, "proxy_resolver", {"default": {"host": "p.test", "port": 3128, "user": "u",
                                                              "password": "p w"}}.get)
    transport.responses = [response()]
    feed = Feed({"fetch": {"proxy": "default", "impersonate": "firefox", "charset": "auto", "timeout": 7}})
    with context.scope(feed=feed):
        fetch.fetch_url("http://t.test/")
        assert transport.calls[-1]["proxy"] == "http://u:p%20w@p.test:3128"
        assert transport.calls[-1]["impersonate"] == "firefox" and transport.calls[-1]["timeout"] == 7
        fetch.fetch_url("http://t.test/", proxy=False, impersonate="safari", timeout=1500)
        assert transport.calls[-1]["proxy"] is None and transport.calls[-1]["impersonate"] == "safari"
        assert transport.calls[-1]["timeout"] == 1.5  # the option is in milliseconds
    fetch.fetch_url("http://t.test/")
    assert transport.calls[-1]["proxy"] is None and transport.calls[-1]["impersonate"] == "chrome"


def test_proxies_from_config(transport, monkeypatch):
    from feedxcavator import conf, telegram

    monkeypatch.setitem(conf.config, "proxies", {
        "default": "socks5h://me:p%40ss@127.0.0.1:1080",
        "work": {"host": "w.test", "port": 3128},
        "broken": {"host": "b.test"},
    })
    monkeypatch.setattr(fetch, "proxy_resolver", {"code": {"host": "c.test", "port": 8080}}.get)
    transport.responses = [response()] * 4
    fetch.fetch_url("http://t.test/", proxy="default")
    assert transport.calls[-1]["proxy"] == "socks5h://me:p%40ss@127.0.0.1:1080"
    fetch.fetch_url("http://t.test/", proxy="work")
    assert transport.calls[-1]["proxy"] == "http://w.test:3128"
    fetch.fetch_url("http://t.test/", proxy="code")  # proxies defined in workspace code still work
    assert transport.calls[-1]["proxy"] == "http://c.test:8080"
    calls = len(transport.calls)
    assert fetch.fetch_url("http://t.test/", proxy="broken") is None  # no port: not usable, fails closed
    assert isinstance(fetch.get_last_network_error(), LookupError) and len(transport.calls) == calls
    tg = telegram._proxy("default")["proxy"]
    assert tg["proxy_type"] == "socks5" and tg["addr"] == "127.0.0.1" and tg["port"] == 1080
    assert tg["username"] == "me" and tg["password"] == "p@ss"
    assert telegram._proxy(None) == {}
    with pytest.raises(LookupError):
        telegram._proxy("missing")  # fails closed, as fetch_url does


def test_mtproto_proxies(transport, monkeypatch):
    from telethon import connection

    from feedxcavator import browser, conf, telegram

    key = "0123456789abcdef0123456789abcdef"
    monkeypatch.setitem(conf.config, "proxies", {
        "plain": {"scheme": "mtproto", "host": "m.test", "port": 443, "secret": key},
        "url": f"mtproto://u.test:8443?secret=dd{key}",
        "link": f"tg://proxy?server=l.test&port=443&secret=dd{key}",
        "web": f"https://t.me/proxy?server=w.test&port=443&secret={key}",
        "faketls": f"tg://proxy?server=f.test&port=443&secret=ee{key}6578616d706c652e636f6d",
        "nosecret": {"scheme": "mtproto", "host": "n.test", "port": 443},
    })
    plain = telegram._proxy("plain")
    assert plain == {"proxy": ("m.test", 443, key), "connection": connection.ConnectionTcpMTProxyIntermediate}
    for name, host, port in [("url", "u.test", 8443), ("link", "l.test", 443)]:
        options = telegram._proxy(name)  # dd-secrets need the randomized intermediate protocol
        assert options == {"proxy": (host, port, key),
                           "connection": connection.ConnectionTcpMTProxyRandomizedIntermediate}
    assert telegram._proxy("web")["proxy"] == ("w.test", 443, key)
    with pytest.raises(ValueError, match="fake-TLS"):
        telegram._proxy("faketls")
    with pytest.raises(LookupError):
        telegram._proxy("nosecret")  # not usable without a secret: fails closed
    # an MTProto proxy cannot carry HTTP: fetches and browsers refuse it instead of connecting directly
    assert fetch.fetch_url("http://t.test/", proxy="plain") is None and not transport.calls
    assert "MTProto" in str(fetch.get_last_network_error())
    with pytest.raises(browser.BrowserError, match="MTProto"):
        with browser.browser(proxy="link"):
            pass


def test_telegram_proxy_choice(monkeypatch):
    from feedxcavator import conf, context, telegram

    monkeypatch.setitem(conf.config, "telegram", None)
    assert telegram.proxy_name() is None  # a direct connection unless something names a proxy
    with context.scope(feed={"fetch": {"proxy": "work"}}):
        assert telegram.proxy_name() == "work"
        assert telegram.proxy_name("other") == "other" and telegram.proxy_name(False) is False
    with context.scope(feed={"fetch": {}}):
        assert telegram.proxy_name() is None
    monkeypatch.setitem(conf.config, "telegram", {"use-proxy": "tg"})
    assert telegram.proxy_name() == "tg"  # use-proxy is used for every call
    with context.scope(feed={"fetch": {"proxy": "work"}}):
        assert telegram.proxy_name() == "tg"
        assert telegram.proxy_name("other") == "tg" and telegram.proxy_name(False) == "tg"


def test_telegram_settings(monkeypatch):
    from feedxcavator import conf, telegram

    monkeypatch.setitem(conf.config, "telegram", None)
    monkeypatch.setitem(conf.config, "tg-api-id", None)
    monkeypatch.setitem(conf.config, "tg-api-hash", None)
    assert not telegram.configured()
    monkeypatch.setitem(conf.config, "tg-api-id", 123)  # the 3.0.0 keys
    monkeypatch.setitem(conf.config, "tg-api-hash", "abc")
    assert telegram.configured() and conf.telegram("api-id") == 123
    monkeypatch.setitem(conf.config, "telegram", {"api-id": 456, "api-hash": "def"})
    assert conf.telegram("api-id") == 456 and conf.telegram("api-hash") == "def"


def test_telegram_forward_passes_feed_proxy(monkeypatch):
    from feedxcavator import context, rpc, telegram

    calls = []
    monkeypatch.setattr(telegram, "in_worker", False)
    monkeypatch.setattr(rpc, "call", lambda method, *args, **kwargs: calls.append((method, args, kwargs)) or [])
    with context.scope(feed={"fetch": {"proxy": "work"}}):
        telegram.get_channel_messages("chan", 5)
        telegram.notify("hi", 42)
    assert calls == [("telegram_get_channel_messages", ("chan", 5), {"proxy": "work"}),
                     ("telegram_notify", ("hi", 42), {"proxy": "work"})]


def test_log_fetch_errors(transport):
    transport.responses = [response(500)]
    fetch.fetch_url("http://t.test/quiet", retry=False)
    assert LogEntry.objects.count() == 0
    with fetch.log_fetch_errors(), context.scope(source="my-extractor"):
        fetch.fetch_url("http://t.test/loud", retry=False)
    entry = LogEntry.objects.get()
    assert entry.level == "error" and entry.source == "my-extractor"
    assert "http://t.test/loud" in entry.message and "500" in entry.message
    assert applog.read_entries(5)[0]["number"] == entry.id


def test_browser_session_hand_over(transport):
    fetch.adopt_browser_session("https://shop.test/x", {"cf_clearance": "abc"}, "UA/1.0", "chrome")
    transport.responses = [response()]
    fetch.fetch_url("https://shop.test/list")
    assert transport.calls[-1]["cookies"] == {"cf_clearance": "abc"}
    assert transport.calls[-1]["headers"]["User-Agent"] == "UA/1.0"
    fetch.fetch_url("https://other.test/")
    assert transport.calls[-1]["cookies"] == {}
    transport.responses = [response(403)]
    fetch.fetch_url("https://shop.test/list")  # rejected: the session is dropped
    transport.responses = [response()]
    fetch.fetch_url("https://shop.test/list")
    assert transport.calls[-1]["cookies"] == {}


def test_real_request_against_local_server(http):
    http.routes["/page"] = (200, {"Content-Type": "text/html; charset=utf-8"}, "<h1>Hello</h1>")
    http.routes["/gone"] = (410, {}, "gone")
    assert fetch.fetch_url(http.url + "/page", as_="html").css("h1::text").get() == "Hello"
    assert fetch.fetch_url(http.url + "/gone") is None and fetch.get_last_http_error() == 410
    assert fetch.fetch_url("http://127.0.0.1:9/", retry=False, timeout=2000) is None
    assert fetch.get_last_network_error() is not None


def test_undefined_proxy_fails_closed(transport, monkeypatch):
    monkeypatch.setattr(fetch, "proxy_resolver", {}.get)
    transport.responses = [response()]
    assert fetch.fetch_url("http://t.test/", proxy="nope") is None
    assert isinstance(fetch.get_last_network_error(), LookupError) and transport.calls == []
    assert "proxy 'nope' is not defined" in LogEntry.objects.get().message  # logged even outside log_fetch_errors
    with context.scope(feed=Feed({"fetch": {"proxy": "typo"}})):
        assert fetch.fetch_url("http://t.test/") is None and transport.calls == []
        assert fetch.fetch_url("http://t.test/", async_=True) is None and transport.calls == []
        assert fetch.fetch_url("http://t.test/", proxy=False) is not None  # an explicit direct connection
    assert len(transport.calls) == 1


def test_no_follow_redirects_returns_the_redirect(transport):
    transport.responses = [response(302, b"", headers={"Location": "/next"})]
    redirect = fetch.fetch_url("http://t.test/", follow_redirects=False)
    assert redirect.status == 302 and redirect.header("location") == "/next"
    assert fetch.fetch_url("http://t.test/") is None and fetch.get_last_http_error() == 302  # followed: a failure


def test_request_time_budget(transport):
    transport.responses = [response()]
    with context.scope(deadline=time.monotonic() + 5):
        fetch.fetch_url("http://t.test/", timeout=30000)
    assert transport.calls[-1]["timeout"] <= 5  # an attempt is cut to the time left

    transport.calls.clear()
    transport.responses = [response(503, headers={"Retry-After": "3"})]
    with context.scope(deadline=time.monotonic() + 3):
        assert fetch.fetch_url("http://t.test/") is None
    assert len(transport.calls) == 1 and fetch.get_last_http_error() == 503  # the retry would not fit

    transport.calls.clear()
    with context.scope(deadline=time.monotonic() - 1):
        assert fetch.fetch_url("http://t.test/") is None
    assert transport.calls == [] and isinstance(fetch.get_last_network_error(), fetch.BudgetExceeded)

    transport.responses = [response(503), response()]
    assert fetch.fetch_url("http://t.test/") is not None  # outside a feed request nothing changes
