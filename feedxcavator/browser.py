"""Browser automation.

User code sees one interface: `with browser(profile) as b: page = b.open(url)`. Browsers live in a pool
owned by the worker. In the web process the same interface talks to the worker over RPC.
The engine is any Playwright-compatible one: camoufox (default), patchright or playwright.
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

LAUNCH_TIMEOUT = 120  # seconds a browser may take to start
DEFAULT_WAIT_TIMEOUT = 300  # seconds a job waits for a free browser (`browser-wait-timeout`)


class BrowserError(RuntimeError):
    pass


def _not_installed(engine: str) -> str:
    return (f"browser-engine is {engine}, but it is not installed: run "
            f"python3 install.py --with-browser={engine} in the installation directory (see the manual)")


# the engine side (worker only) ----------------------------------------------------------------


def _headless() -> bool:
    setting = conf.get("browser-headless")
    if setting is not None:
        return bool(setting)
    if os.name == "posix" and not _is_macos():
        return not os.environ.get("DISPLAY")  # headful when a (virtual) display exists
    return True


def _is_macos() -> bool:
    import sys

    return sys.platform == "darwin"


_xvfb = None


def ensure_display():
    """On Linux without a display, use `browser-display` (an X display the worker may use, such as the
    desktop's ":0" opened with `xhost +`), otherwise start Xvfb when it is installed, so the browser runs headful."""
    global _xvfb
    if os.name != "posix" or _is_macos() or os.environ.get("DISPLAY") or conf.get("browser-headless") is True:
        return
    if conf.get("browser-display"):
        os.environ["DISPLAY"] = str(conf.get("browser-display"))
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

    def __init__(self, profile: str, proxy: dict | None = None):
        self.profile = storage.sanitize_path(profile or "default").replace("/", "_").replace("\\", "_")
        self.proxy = proxy  # a resolved proxy definition (fetch.resolve_proxy), applied at launch
        self._commands: queue.Queue = queue.Queue()
        self._pages: dict[int, object] = {}
        self._challenged: dict[int, bool] = {}  # pages whose last navigation Cloudflare answered with a challenge
        self._next_page = 1
        self._context = None
        self._headless = True
        self._closers: list = []
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"browser-{self.profile}")
        self._thread.start()
        if not self._ready.wait(LAUNCH_TIMEOUT):
            # the launch may still finish: tell the thread to close the browser then, and wait for it, so the
            # profile directory is not released to another browser while this one is still starting on it
            self._commands.put(None)
            self._thread.join(60)
        if self._error is not None or self._context is None:
            raise BrowserError(f"failed to start the browser: {self._error or 'timeout'}")

    def _launch(self):
        engine = str(conf.get("browser-engine", "camoufox")).lower()
        profile_dir = conf.DATA_DIR / "browser" / self.profile
        profile_dir.mkdir(parents=True, exist_ok=True)
        headless = self._headless = _headless()
        if engine == "camoufox":
            try:
                import camoufox  # noqa: F401
            except ImportError:
                raise BrowserError(_not_installed(engine)) from None

            from feedxcavator import camoufox_identity

            extra = {"proxy": playwright_proxy(self.proxy)} if self.proxy else {}
            manager, self._context = camoufox_identity.start(profile_dir, headless, **extra)
            self._closers.append(lambda: manager.__exit__(None, None, None))
            return
        try:
            if engine == "playwright":
                from playwright.sync_api import sync_playwright
            else:
                from patchright.sync_api import sync_playwright
        except ImportError:
            raise BrowserError(_not_installed(engine)) from None
        playwright = sync_playwright().start()
        self._closers.append(playwright.stop)
        channel = conf.get("browser-channel", "chrome")
        options = {"user_data_dir": str(profile_dir), "headless": headless, "no_viewport": True}
        if self.proxy:
            options["proxy"] = playwright_proxy(self.proxy)
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

    def _free_page(self):
        """A page not in use: the blank page a persistent context starts with, or a page a job closed
        last. Firefox (camoufox) opens each new page in a window of its own, so reusing them keeps the
        browser at one window."""
        used = list(self._pages.values())
        for page in self._context.pages:
            if page not in used and not page.is_closed():
                return page
        return self._context.new_page()

    def _op_open(self, url, wait_for=None, scroll=0, timeout=30):
        page = self._free_page()
        page_id = self._next_page
        self._next_page += 1
        self._pages[page_id] = page
        try:
            self._navigate(page_id, url, timeout)
            if wait_for:
                page.wait_for_selector(wait_for, timeout=timeout * 1000)
            self._op_scroll(page_id, scroll)
        except BaseException:
            # no Page object reaches the caller, so nothing would close it: free it here
            try:
                self._op_close_page(page_id)
            except Exception:  # noqa: BLE001
                self._pages.pop(page_id, None)
            raise
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

    def _op_wait_for(self, page_id, selector, timeout=30, required=True):
        try:
            self._page(page_id).wait_for_selector(selector, timeout=timeout * 1000)
        except Exception as e:
            if required or "timeout" not in type(e).__name__.lower():
                raise
            return False
        return True

    def _op_wait(self, page_id, seconds):
        self._page(page_id).wait_for_timeout(seconds * 1000)

    def _op_evaluate(self, page_id, script, arg=None):
        return self._page(page_id).evaluate(script, arg)

    def _op_screenshot(self, page_id, selector=None, type="png", quality=None, style=None, timeout=30):
        page = self._page(page_id)
        # scale="css": a fingerprint with devicePixelRatio 2 would double the width (and quadruple the bytes)
        options = {"type": type, "timeout": timeout * 1000, "scale": "css", "animations": "disabled",
                   "caret": "hide"}
        if type == "jpeg" and quality is not None:
            options["quality"] = int(quality)
        if style:
            options["style"] = style
        if selector:
            return page.locator(selector).first.screenshot(**options)
        return page.screenshot(**options)

    def _op_solve_captcha(self, page_id, timeout=60, attempts=2, watch=0):
        from feedxcavator import captcha

        return captcha.solve(self._page(page_id), self._headless, timeout, attempts, watch,
                             expected=self._challenged.get(page_id, False))

    def _navigate(self, page_id, url, timeout):
        response = self._page(page_id).goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        self._challenged[page_id] = bool(response and response.headers.get("cf-mitigated") == "challenge")

    def _op_goto(self, page_id, url, wait_for=None, timeout=30):
        page = self._page(page_id)
        self._navigate(page_id, url, timeout)
        if wait_for:
            page.wait_for_selector(wait_for, timeout=timeout * 1000)

    def _op_close_page(self, page_id):
        page = self._pages.pop(page_id, None)
        self._challenged.pop(page_id, None)
        if page is None:
            return
        if len([p for p in self._context.pages if not p.is_closed()]) > 1:
            page.close()
        else:  # the last page: keep its window for the next open
            for _ in range(2):  # a page navigating by itself (a Cloudflare check reloading) interrupts the goto
                try:
                    page.goto("about:blank")
                    return
                except Exception:  # noqa: BLE001
                    page.wait_for_timeout(500)
            try:
                page.close()
            except Exception:  # noqa: BLE001, S110
                pass

    def _op_cookies(self, url=None):
        cookies = self._context.cookies(url) if url else self._context.cookies()
        return {c["name"]: c["value"] for c in cookies}

    def _op_user_agent(self):
        page = next(iter(self._pages.values()), None) or self._free_page()
        return page.evaluate("navigator.userAgent")


def playwright_proxy(proxy: dict) -> dict:
    """A proxy definition (fetch.resolve_proxy) as the `proxy` launch option of Playwright and Camoufox."""
    scheme = proxy.get("scheme") or proxy.get("type") or "http"
    result = {"server": f"{scheme}://{proxy['host']}:{proxy['port']}"}
    if proxy.get("user"):
        result["username"] = str(proxy["user"])
        result["password"] = str(proxy.get("password") or "")
    return result


class BrowserPool:
    """At most `browser-pool-size` browsers. A job waits for a free one."""

    def __init__(self, size: int | None = None):
        self.size = max(1, int(size or conf.get("browser-pool-size", 1)))
        self._slots = threading.Semaphore(self.size)
        self._profiles: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _take(lock, deadline: float, what: str):
        """Acquires a lock or semaphore, giving up when the job is cancelled or after `browser-wait-timeout`."""
        while not lock.acquire(timeout=max(0.0, min(1.0, deadline - time.monotonic()))):
            context.check_cancelled()
            if time.monotonic() >= deadline:
                wait = float(conf.get("browser-wait-timeout", DEFAULT_WAIT_TIMEOUT))
                raise BrowserError(f"no {what} became free within {wait:g} s (browser-wait-timeout)")

    def acquire(self, profile: str = "default", proxy: dict | None = None) -> _Session:
        with self._lock:
            profile_lock = self._profiles.setdefault(profile, threading.Lock())
        deadline = time.monotonic() + float(conf.get("browser-wait-timeout", DEFAULT_WAIT_TIMEOUT))
        self._take(self._slots, deadline, "browser")
        try:
            # a profile directory can be used by one browser at a time
            self._take(profile_lock, deadline, f"browser for profile '{profile}'")
        except BaseException:
            self._slots.release()
            raise
        try:
            ensure_display()
            session = _Session(profile, proxy)
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
        """The current URL of the page, after redirects and navigations (a property: `page.url`, `(. page url)`
        in Hy)."""
        return self._call("url")

    def selector(self):
        """The live DOM as a parsel selector."""
        from parsel import Selector

        return Selector(text=self.html())

    def apply_selectors(self, feed=None):
        """Applies the feed selectors to the live DOM. Parts are text content where the built-in
        extractor gives inner HTML."""
        feed = feed if feed is not None else context.current_feed.get()
        return sel.apply_selectors(self.html(), feed, base_url=self.url, text_mode=True)

    def goto(self, url, wait_for=None, timeout=30):
        """Navigates the page to `url` and waits for the DOM to load, then for `wait_for` (a CSS selector) when
        given. `timeout` is in seconds. Returns the page, so calls chain. Later fetch_url calls to the host reuse
        the browser's cookies and user agent."""
        self._call("goto", url, wait_for, timeout)
        self._owner._hand_over(url)
        return self

    def click(self, selector, timeout=30):
        """Clicks the first element matching `selector` (CSS, or a Playwright selector such as "text=More"),
        waiting up to `timeout` seconds for it to be visible and enabled. The click is made by the driver, which
        some bot checks ignore (see solve_captcha)."""
        self._call("click", selector, timeout)

    def fill(self, selector, value, timeout=30):
        """Replaces the value of the input or textarea matching `selector` with `value`, waiting up to `timeout`
        seconds for the element."""
        self._call("fill", selector, value, timeout)

    def press(self, selector, key):
        """Focuses the element matching `selector` and presses `key`: a key name such as "Enter", "ArrowDown"
        or "Control+A", or a single character."""
        self._call("press", selector, key)

    def wait_for(self, selector, timeout=30, required=True) -> bool:
        """Waits for an element matching `selector`, through navigations (such as the reload a security check
        does once passed). On a timeout it raises, or with `required=False` returns False."""
        return self._call("wait_for", selector, timeout, required)

    def wait(self, seconds):
        """Pauses for `seconds` with the page live (scripts keep running). Prefer wait_for when there is an
        element to wait for."""
        self._call("wait", seconds)

    def scroll(self, times=1):
        """Scrolls to the bottom of the page `times` times, 0.8 seconds apart, so infinite lists load more
        items."""
        self._call("scroll", times)

    def evaluate(self, script, arg=None):
        """Runs a JavaScript expression in the page and returns its value. When the expression is a function,
        it is called with `arg` (JSON-like data)."""
        return self._call("evaluate", script, arg)

    def screenshot(self, selector=None, type="png", quality=None, style=None, timeout=30) -> bytes:
        """The first element matching `selector`, or the viewport without one, as image bytes. Playwright
        scrolls the element into view first. `type` is "png" or "jpeg" (with `quality` 0-100); `style` is CSS
        applied only while the shot is taken, for example to hide fixed headers lying over the element."""
        return self._call("screenshot", selector, type, quality, style, timeout)

    def solve_captcha(self, timeout=60, attempts=2, watch=0) -> bool:
        """Passes a Cloudflare challenge on the page with the system mouse, if there is one. True when
        the page is through. `watch` is how many seconds to keep looking for a challenge that has not shown yet
        (it returns as soon as one does). Needs a headful browser, pyautogui and the display the browser is on."""
        solved = self._call("solve_captcha", timeout, attempts, watch)
        if solved:
            self._owner._hand_over(self.url)
        return solved

    def text(self, selector=None):
        """The text content of the first element matching `selector` (None when nothing matches), or of the
        whole document without one."""
        document = self.selector()
        return sel.text(sel.q1(document, selector) if selector else document)

    def close(self):
        """Closes the page. Pages left open are closed with the browser at the end of the `with` block."""
        self._call("close_page")


class Browser:
    def __init__(self, transport):
        self._transport = transport

    def _call(self, op, *args, **kwargs):
        context.check_cancelled()
        return self._transport.call(op, *args, **kwargs)

    def open(self, url, wait_for=None, scroll=0, timeout=30) -> Page:
        """Opens `url` in a new page and returns it. `wait_for` is a CSS selector to wait for after the DOM
        loads, `scroll` a number of scrolls to the bottom (for infinite lists), `timeout` the limit in seconds
        for the navigation and for `wait_for`. Raises BrowserError when the page does not load. Later fetch_url
        calls to the host reuse the browser's cookies and user agent."""
        page = Page(self, self._call("open", url, wait_for, scroll, timeout))
        self._hand_over(url)
        return page

    def cookies(self, url=None) -> dict:
        """The cookies of the browser profile as a {name: value} dict, only those sent to `url` when given."""
        return self._call("cookies", url)

    @property
    def user_agent(self) -> str:
        """The browser's user agent string (a property: `b.user_agent`)."""
        return self._call("user_agent")

    def _hand_over(self, url):
        """Later fetch_url calls for the host reuse the cookies and user agent."""
        try:
            host_url = "{0.scheme}://{0.netloc}/".format(urlsplit(url))
            engine = str(conf.get("browser-engine", "camoufox")).lower()
            fetch.adopt_browser_session(url, self._call("cookies", host_url), self._call("user_agent"),
                                        "firefox" if engine == "camoufox" else "chrome")
        except Exception as e:  # noqa: BLE001
            logger.debug("session hand-over failed: %s", e)


class _LocalTransport:
    def __init__(self, pool: BrowserPool, profile: str, proxy: dict | None = None):
        self._pool = pool
        self._session = pool.acquire(profile, proxy)

    def call(self, op, *args, **kwargs):
        return self._session.call(op, *args, **kwargs)

    def close(self):
        self._pool.release(self._session)


class _RemoteTransport:
    def __init__(self, profile: str, proxy: dict | None = None):
        from feedxcavator import rpc

        self._connection = rpc.connect()
        self._request("browser_open", profile, proxy)

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
    """with browser(profile="default", proxy=None) as b: ...

    `proxy` names a proxy (defproxy, or `proxies` in config.yml) the browser connects through. The browser is
    launched for each `with`, so the proxy holds for that session only; the profile keeps its cookies."""

    def __init__(self, profile="default", proxy=None, **options):
        self._profile = str(getattr(options.get("profile"), "name", None) or profile or "default")
        self._proxy_name = proxy
        self._transport = None

    def __enter__(self) -> Browser:
        context.check_cancelled()
        proxy = None
        if self._proxy_name:
            proxy = fetch.resolve_proxy(self._proxy_name)
            if proxy is None:
                raise BrowserError(f"browser: proxy '{self._proxy_name}' is not defined")
            if fetch.is_mtproto(proxy):
                raise BrowserError(f"browser: proxy '{self._proxy_name}' is an MTProto proxy, usable only by Telegram")
        if _pool is not None:
            self._transport = _LocalTransport(_pool, self._profile, proxy)
        else:
            try:
                self._transport = _RemoteTransport(self._profile, proxy)
            except (OSError, EOFError) as e:
                raise BrowserError(
                    f"the browser runs in the worker process, which is not reachable: {e}") from None
        return Browser(self._transport)

    def __exit__(self, *exc):
        if self._transport is not None:
            self._transport.close()
        return False
