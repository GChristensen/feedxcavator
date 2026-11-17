"""Browser session ops driven with a stub page (no browser is started)."""

import pytest

from feedxcavator import browser


class TimeoutError(Exception):  # noqa: A001 - named as Playwright's
    pass


class _StubPage:
    def __init__(self, appears=True, goto_fails=False):
        self.appears = appears
        self.goto_fails = goto_fails
        self.closed = False

    def wait_for_selector(self, selector, timeout):
        if not self.appears:
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {selector}")

    def goto(self, url, **kwargs):
        if self.goto_fails:
            raise RuntimeError("net::ERR_CONNECTION_RESET")

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def evaluate(self, script):
        pass

    def wait_for_timeout(self, ms):
        pass


class _StubContext:
    def __init__(self, pages):
        self.pages = pages

    def new_page(self):
        raise AssertionError("no new page expected")


def _session(page):
    session = object.__new__(browser._Session)
    session._pages = {}
    session._challenged = {}
    session._next_page = 1
    session._context = _StubContext([page, _StubPage()])
    return session


def test_wait_for_not_required_returns_false_on_timeout():
    session = _session(_StubPage(appears=False))
    session._pages[1] = session._context.pages[0]
    assert session._op_wait_for(1, "#x", 1, required=False) is False
    with pytest.raises(TimeoutError):
        session._op_wait_for(1, "#x", 1)


def test_wait_for_returns_true_when_found():
    session = _session(_StubPage())
    session._pages[1] = session._context.pages[0]
    assert session._op_wait_for(1, "#x", 1, required=False) is True


def test_open_frees_the_page_when_navigation_fails():
    page = _StubPage(goto_fails=True)
    session = _session(page)
    with pytest.raises(RuntimeError):
        session._op_open("https://example.com/")
    assert session._pages == {}
    assert page.closed


def test_close_page_survives_an_interrupted_goto():
    page = _StubPage(goto_fails=True)
    session = _session(page)
    session._pages[1] = page
    session._op_close_page(1)
    assert session._pages == {}
    assert page.closed


def test_playwright_proxy():
    assert browser.playwright_proxy({"host": "p.test", "port": 3128}) == {"server": "http://p.test:3128"}
    assert browser.playwright_proxy({"host": "p.test", "port": 1080, "scheme": "socks5", "user": "u",
                                     "password": "p"}) == {"server": "socks5://p.test:1080", "username": "u",
                                                           "password": "p"}


def test_browser_with_an_undefined_proxy_raises(monkeypatch):
    from feedxcavator import fetch

    monkeypatch.setattr(fetch, "proxy_resolver", {}.get)
    with pytest.raises(browser.BrowserError, match="not defined"):
        with browser.browser(proxy="missing"):
            pass
