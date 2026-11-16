"""Browser automation (BRW-1 to BRW-7).

User code sees one interface: `with browser(profile) as b: page = b.open(url)`. Browsers live in a pool
owned by the worker. In the web process the same interface talks to the worker over RPC (PRC-4).
The engine is any Playwright-compatible one: patchright (default), camoufox or playwright.
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit

from feedxcavator import conf, context, fetch, selectors as sel, storage

logger = logging.getLogger("feedxcavator.browser")

_pool: "BrowserPool | None" = None  # set in the worker


class BrowserError(RuntimeError):
    pass


# the engine side (worker only) ----------------------------------------------------------------


def _headless() -> bool:
    setting = conf.get("browser-headless")
    if setting is not None:
        return bool(setting)
    if os.name == "posix" and not _is_macos():
        return not os.environ.get("DISPLAY")  # BRW-4: headful when a (virtual) display exists
    return True


def _is_macos() -> bool:
    import sys

    return sys.platform == "darwin"


_xvfb = None


def ensure_display():
    """BRW-4: on Linux without a display, start Xvfb when it is installed so the browser can run headful."""
    global _xvfb
    if os.name != "posix" or _is_macos() or os.environ.get("DISPLAY") or conf.get("browser-headless") is True:
        return
    binary = shutil.which("Xvfb")
    if not binary or _xvfb is not None:
        return
    display = f":{90 + os.getpid() % 100}"
    try:
        _xvfb = subprocess.Popen([binary, display, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],  # noqa: S603
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        if _xvfb.poll() is None:
            os.environ["DISPLAY"] = display
            logger.info("started Xvfb on %s", display)
        else:
            _xvfb = None
    except OSError as e:
        logger.warning("failed to start Xvfb: %s", e)


class _Session:
    """One browser with a persistent profile, driven on its own thread (the Playwright sync API is
    bound to the thread that created it)."""

    def __init__(self, profile: str):
        self.profile = storage.sanitize_path(profile or "default").replace("/", "_").replace("\\", "_")
        self._commands: queue.Queue = queue.Queue()
        self._pages: dict[int, object] = {}
        self._next_page = 1
        self._context = None
        self._closers: list = []
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"browser-{self.profile}")
        self._thread.start()
        self._ready.wait(120)
        if self._error is not None or self._context is None:
            raise BrowserError(f"failed to start the browser: {self._error or 'timeout'}")

    def _launch(self):
        engine = str(conf.get("browser-engine", "patchright")).lower()
        profile_dir = conf.DATA_DIR / "browser" / self.profile
        profile_dir.mkdir(parents=True, exist_ok=True)
        headless = _headless()
        if engine == "camoufox":
            try:
                from camoufox.sync_api import Camoufox
            except ImportError:
                raise BrowserError("browser-engine is camoufox, but it is not installed: run "
                                   "uv pip install --python .venv/bin/python camoufox, then "
                                   ".venv/bin/python -m camoufox fetch (see the README)") from None

            manager = Camoufox(persistent_context=True, user_data_dir=str(profile_dir), headless=headless)
            self._context = manager.__enter__()
            self._closers.append(lambda: manager.__exit__(None, None, None))
            return
        if engine == "playwright":
            from playwright.sync_api import sync_playwright
        else:
            from patchright.sync_api import sync_playwright
        playwright = sync_playwright().start()
        self._closers.append(playwright.stop)
        channel = conf.get("browser-channel", "chrome")
        options = {"user_data_dir": str(profile_dir), "headless": headless, "no_viewport": True}
        try:
            self._context = playwright.chromium.launch_persistent_context(channel=channel, **options)
        except Exception as e:  # noqa: BLE001 - the channel browser is not installed: use the bundled one
            logger.warning("browser channel '%s' is not available (%s), using the bundled Chromium", channel,
                           str(e).splitlines()[0])
            self._context = playwright.chromium.launch_persistent_context(**options)
        self._closers.append(self._context.close)

    def _run(self):
        try:
            self._launch()
        except BaseException as e:  # noqa: BLE001
            self._error = e
            self._ready.set()
            self._cleanup()
            return
        self._ready.set()
        while True:
            item = self._commands.get()
            if item is None:
                break
            op, args, kwargs, reply = item
            try:
                reply.put((True, getattr(self, f"_op_{op}")(*args, **kwargs)))
            except BaseException as e:  # noqa: BLE001
                reply.put((False, f"{type(e).__name__}: {e}"))
        self._cleanup()

    def _cleanup(self):
        for closer in reversed(self._closers):
            try:
                closer()
            except Exception:  # noqa: BLE001, S110
                pass
        self._closers.clear()

    def call(self, op: str, *args, **kwargs):
        if not self._thread.is_alive():
            raise BrowserError("the browser is closed")
        reply: queue.Queue = queue.Queue()
        self._commands.put((op, args, kwargs, reply))
        ok, value = reply.get()
        if not ok:
            raise BrowserError(value)
        return value

    def close(self):
        self._commands.put(None)
        self._thread.join(30)

    # operations, executed on the browser thread
    def _page(self, page_id):
        return self._pages[page_id]

    def _op_open(self, url, wait_for=None, scroll=0, timeout=30):
        page = self._context.new_page()
        page_id = self._next_page
        self._next_page += 1
        self._pages[page_id] = page
        page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        if wait_for:
            page.wait_for_selector(wait_for, timeout=timeout * 1000)
        self._op_scroll(page_id, scroll)
        return page_id

    def _op_scroll(self, page_id, times=1):
        page = self._page(page_id)
        for _ in range(int(times or 0)):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)

    def _op_html(self, page_id):
        return self._page(page_id).content()

    def _op_url(self, page_id):
        return self._page(page_id).url

    def _op_click(self, page_id, selector, timeout=30):
        self._page(page_id).click(selector, timeout=timeout * 1000)

    def _op_fill(self, page_id, selector, value, timeout=30):
        self._page(page_id).fill(selector, value, timeout=timeout * 1000)

    def _op_press(self, page_id, selector, key):
        self._page(page_id).press(selector, key)

    def _op_wait_for(self, page_id, selector, timeout=30):
        self._page(page_id).wait_for_selector(selector, timeout=timeout * 1000)

    def _op_wait(self, page_id, seconds):
        self._page(page_id).wait_for_timeout(seconds * 1000)

    def _op_evaluate(self, page_id, script):
        return self._page(page_id).evaluate(script)

    def _op_goto(self, page_id, url, wait_for=None, timeout=30):
        page = self._page(page_id)
        page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        if wait_for:
            page.wait_for_selector(wait_for, timeout=timeout * 1000)

    def _op_close_page(self, page_id):
        page = self._pages.pop(page_id, None)
        if page is not None:
            page.close()

    def _op_cookies(self, url=None):
        cookies = self._context.cookies(url) if url else self._context.cookies()
        return {c["name"]: c["value"] for c in cookies}

    def _op_user_agent(self):
        page = next(iter(self._pages.values()), None) or self._context.new_page()
        return page.evaluate("navigator.userAgent")


class BrowserPool:
    """BRW-3: at most `browser-pool-size` browsers. A job waits for a free one."""

    def __init__(self, size: int | None = None):
        self.size = max(1, int(size or conf.get("browser-pool-size", 1)))
        self._slots = threading.Semaphore(self.size)
        self._profiles: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def acquire(self, profile: str = "default") -> _Session:
        with self._lock:
            profile_lock = self._profiles.setdefault(profile, threading.Lock())
        self._slots.acquire()
        profile_lock.acquire()  # a profile directory can be used by one browser at a time
        try:
            ensure_display()
            session = _Session(profile)
        except BaseException:
            profile_lock.release()
            self._slots.release()
            raise
        session._release = lambda: (profile_lock.release(), self._slots.release())
        return session

    def release(self, session: _Session):
        try:
            session.close()
        finally:
            release = getattr(session, "_release", None)
            if release:
                session._release = None
                release()


def start_pool() -> BrowserPool:
    global _pool
    _pool = BrowserPool()
    return _pool


# the interface seen by user code ----------------------------------------------------------------


class Page:
    def __init__(self, owner: "Browser", page_id: int):
        self._owner = owner
        self._id = page_id

    def _call(self, op, *args, **kwargs):
        return self._owner._call(op, self._id, *args, **kwargs)

    def html(self) -> str:
        """The HTML of the live DOM."""
        return self._call("html")

    @property
    def url(self) -> str:
        return self._call("url")

    def selector(self):
        """The live DOM as a parsel selector."""
        from parsel import Selector

        return Selector(text=self.html())

    def apply_selectors(self, feed=None):
        """Applies the feed selectors to the live DOM. Parts are text content where the built-in
        extractor gives inner HTML (INT-1)."""
        feed = feed if feed is not None else context.current_feed.get()
        return sel.apply_selectors(self.html(), feed, base_url=self.url, text_mode=True)

    def goto(self, url, wait_for=None, timeout=30):
        self._call("goto", url, wait_for, timeout)
        self._owner._hand_over(url)
        return self

    def click(self, selector, timeout=30):
        self._call("click", selector, timeout)

    def fill(self, selector, value, timeout=30):
        self._call("fill", selector, value, timeout)

    def press(self, selector, key):
        self._call("press", selector, key)

    def wait_for(self, selector, timeout=30):
        self._call("wait_for", selector, timeout)

    def wait(self, seconds):
        self._call("wait", seconds)

    def scroll(self, times=1):
        self._call("scroll", times)

    def evaluate(self, script):
        return self._call("evaluate", script)

    def text(self, selector=None):
        document = self.selector()
        return sel.text(sel.q1(document, selector) if selector else document)

    def close(self):
        self._call("close_page")


class Browser:
    def __init__(self, transport):
        self._transport = transport

    def _call(self, op, *args, **kwargs):
        context.check_cancelled()
        return self._transport.call(op, *args, **kwargs)

    def open(self, url, wait_for=None, scroll=0, timeout=30) -> Page:
        """Opens a URL in a new tab. `wait_for` is a CSS selector to wait for, `scroll` a number of
        scrolls to the bottom (for infinite lists)."""
        page = Page(self, self._call("open", url, wait_for, scroll, timeout))
        self._hand_over(url)
        return page

    def cookies(self, url=None) -> dict:
        return self._call("cookies", url)

    @property
    def user_agent(self) -> str:
        return self._call("user_agent")

    def _hand_over(self, url):
        """BRW-5: later fetch_url calls for the host reuse the cookies and user agent."""
        try:
            host_url = "{0.scheme}://{0.netloc}/".format(urlsplit(url))
            engine = str(conf.get("browser-engine", "patchright")).lower()
            fetch.adopt_browser_session(url, self._call("cookies", host_url), self._call("user_agent"),
                                        "firefox" if engine == "camoufox" else "chrome")
        except Exception as e:  # noqa: BLE001
            logger.debug("session hand-over failed: %s", e)


class _LocalTransport:
    def __init__(self, pool: BrowserPool, profile: str):
        self._pool = pool
        self._session = pool.acquire(profile)

    def call(self, op, *args, **kwargs):
        return self._session.call(op, *args, **kwargs)

    def close(self):
        self._pool.release(self._session)


class _RemoteTransport:
    def __init__(self, profile: str):
        from feedxcavator import rpc

        self._connection = rpc.connect()
        self._request("browser_open", profile)

    def _request(self, method, *args, **kwargs):
        self._connection.send((method, args, kwargs))
        status, value = self._connection.recv()
        if status != "ok":
            raise BrowserError(value)
        return value

    def call(self, op, *args, **kwargs):
        return self._request("browser_call", op, args, kwargs)

    def close(self):
        try:
            self._request("browser_close")
        finally:
            self._connection.close()


class browser:
    """with browser(profile="default") as b: ..."""

    def __init__(self, profile="default", **options):
        self._profile = str(getattr(options.get("profile"), "name", None) or profile or "default")
        self._transport = None

    def __enter__(self) -> Browser:
        context.check_cancelled()
        if _pool is not None:
            self._transport = _LocalTransport(_pool, self._profile)
        else:
            try:
                self._transport = _RemoteTransport(self._profile)
            except (OSError, EOFError) as e:
                raise BrowserError(
                    f"the browser runs in the worker process, which is not reachable: {e}") from None
        return Browser(self._transport)

    def __exit__(self, *exc):
        if self._transport is not None:
            self._transport.close()
        return False
