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
        fetch.fetch_url("http://t.test/", as_="edn")  # FT2-3


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
    assert 2.0 <= transport.sleeps[0] <= 2.2  # never shorter, extended by up to 10% (FT-5)

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
    assert fetch.fetch_url("http://t.test/", as_="string") == "Привет"  # from the response (fixes 17.4)
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
        assert transport.calls[-1]["timeout"] == 1.5  # the option is in milliseconds (FT-1)
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
    fetch.fetch_url("http://t.test/", proxy="broken")  # no port: a direct connection
    assert transport.calls[-1]["proxy"] is None
    tg = telegram._proxy()
    assert tg["proxy_type"] == "socks5" and tg["addr"] == "127.0.0.1" and tg["port"] == 1080
    assert tg["username"] == "me" and tg["password"] == "p@ss"


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
    fetch.fetch_url("https://shop.test/list")  # rejected: the session is dropped (BRW-5)
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
