"""Cloudflare challenges solved with the system mouse.

Turnstile ignores clicks synthesized by the browser driver, so the pointer is moved and clicked by pyautogui
on the display the browser window is on. The checkbox is not clicked at fixed screen coordinates: its position
comes from the widget's box in the page, and page coordinates are mapped to the screen by a calibration move,
with a `mousemove` listener reporting where the real pointer landed in the page. The window geometry gives the
first estimate, `captcha-position` in config.yml a last fallback.

Runs in the worker, on the browser session's thread, with the Playwright page. One solve at a time, since there
is one pointer.
"""

from __future__ import annotations

import logging
import os
import random
import sys
import threading
import time
from dataclasses import dataclass

from feedxcavator import conf, context

logger = logging.getLogger("feedxcavator.captcha")

MARGIN = 0.15  # random pointer moves keep this share of the screen size off each edge
WANDER_MOVES = 2
CHECKBOX_X = 30  # the checkbox centre, in CSS pixels from the left edge of the Turnstile widget
CLEAR_TIMEOUT = 20  # seconds to wait for the challenge to clear after a click
CHALLENGE_FRAME = "challenges.cloudflare.com"

_lock = threading.Lock()

# challenge state of the page: an interstitial or an unsolved embedded widget. The interstitial is recognized by
# its markup, not only its title, which Cloudflare translates. Scripts under /cdn-cgi/challenge-platform/ alone
# do not count: Cloudflare adds one (jsd) to ordinary pages too.
_STATE_JS = """() => {
    const interstitial = /^just a moment/i.test(document.title)
        || !!document.querySelector("#challenge-form, #challenge-running, #challenge-stage, #challenge-error-text, "
            + "form[action*='__cf_chl'], script[src*='/orchestrate/chl_page/'], script[src*='/orchestrate/managed/'], "
            + "[id^='cf-chl-widget']");
    const response = document.querySelector("input[name='cf-turnstile-response']");
    const widget = !!document.querySelector(".cf-turnstile, iframe[src*='challenges.cloudflare.com']") || !!response;
    return {challenge: interstitial || (widget && !(response && response.value))};
}"""

# the widget box when its iframe is not reachable through page.frames (CSS pixels, viewport-relative)
_WIDGET_BOX_JS = """() => {
    const candidates = [
        document.querySelector("iframe[src*='challenges.cloudflare.com']"),
        document.querySelector(".cf-turnstile"),
        document.querySelector("input[name='cf-turnstile-response']")?.parentElement,
        document.querySelector("#challenge-stage"),
    ];
    for (const el of candidates) {
        const r = el?.getBoundingClientRect();
        if (r && r.width > 20 && r.height > 20) return {x: r.x, y: r.y, width: r.width, height: r.height};
    }
    return null;
}"""

_GEOMETRY_JS = """() => ({screenX: window.screenX, screenY: window.screenY,
    outerWidth: window.outerWidth, outerHeight: window.outerHeight,
    innerWidth: window.innerWidth, innerHeight: window.innerHeight, dpr: window.devicePixelRatio || 1})"""

# the last pointer position the page saw; kept on a non-enumerable property of the listener's own name
_TRACK_JS = """() => {
    const key = "__fxPointer";
    if (!Object.getOwnPropertyDescriptor(window, key)) {
        Object.defineProperty(window, key, {value: {at: null}, enumerable: false});
        window.addEventListener("mousemove", e => { window[key].at = [e.clientX, e.clientY]; },
                                {capture: true, passive: true});
    }
    window[key].at = null;
}"""

_POINTER_JS = "() => window.__fxPointer ? window.__fxPointer.at : null"


# geometry, kept free of the browser and the display so it can be tested ---------------------------


@dataclass(frozen=True)
class ScreenMap:
    """Maps page (viewport CSS) coordinates to screen pixels: screen = css * scale + offset."""

    offset_x: float
    offset_y: float
    scale: float = 1.0

    @classmethod
    def estimate(cls, g: dict) -> "ScreenMap":
        """From the window geometry: side borders are split evenly and the rest of the height is above the
        viewport (title bar, tabs, toolbar)."""
        border = max(0, g["outerWidth"] - g["innerWidth"]) / 2
        top = max(0, g["outerHeight"] - g["innerHeight"] - border)
        dpr = g.get("dpr") or 1
        return cls((g["screenX"] + border) * dpr, (g["screenY"] + top) * dpr, dpr)

    @classmethod
    def calibrated(cls, screen_point: tuple[float, float], page_point: tuple[float, float], scale: float):
        """From a pointer at `screen_point` that the page reported at `page_point`."""
        return cls(screen_point[0] - page_point[0] * scale, screen_point[1] - page_point[1] * scale, scale)

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return round(x * self.scale + self.offset_x), round(y * self.scale + self.offset_y)


def random_point(width: int, height: int, margin: float = MARGIN) -> tuple[int, int]:
    """A random screen point at least `margin` of the screen size away from every edge."""
    return (random.randint(int(width * margin), int(width * (1 - margin))),
            random.randint(int(height * margin), int(height * (1 - margin))))


def checkbox_point(box: dict) -> tuple[float, float]:
    """The Turnstile checkbox in a widget box, with a little jitter."""
    return (box["x"] + min(CHECKBOX_X, box["width"] / 2) + random.uniform(-2, 2),
            box["y"] + box["height"] / 2 + random.uniform(-2, 2))


# the pointer --------------------------------------------------------------------------------------


def _xauthority_fallback(environ=os.environ):
    """python-xlib, which pyautogui connects with, fails when XAUTHORITY is unset and ~/.Xauthority does not
    exist, although a display without access control (the worker's Xvfb, a desktop opened with `xhost +`)
    needs no cookie. An empty authority file makes it connect without one, as libX11 does."""
    if not sys.platform.startswith("linux") or environ.get("XAUTHORITY"):
        return
    home = environ.get("HOME")
    if not home or not os.path.exists(os.path.join(home, ".Xauthority")):
        environ["XAUTHORITY"] = os.devnull


def _gui():
    _xauthority_fallback()
    try:
        import pyautogui
    except ImportError:
        raise RuntimeError("solving captchas needs pyautogui: run uv pip install --python .venv/bin/python "
                           "pyautogui (see the README)") from None
    except Exception as e:  # noqa: BLE001 - Xlib raises when the display is not reachable
        raise RuntimeError(f"solving captchas needs a display the worker can reach: {e}") from None
    pyautogui.PAUSE = 0.05
    return pyautogui


def _flash(x: int, y: int, size: int = 14, duration: int = 700):
    """`captcha-flash: true`: a marker where the pointer is about to act, to check the coordinates."""
    if not conf.get("captcha-flash"):
        return
    try:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.7)
        root.geometry(f"{size}x{size}+{x - size // 2}+{y - size // 2}")
        canvas = tk.Canvas(root, width=size, height=size, bg="red", highlightthickness=0)
        canvas.create_oval(2, 2, size - 2, size - 2, fill="yellow", outline="red", width=3)
        canvas.pack()
        root.after(duration, root.destroy)
        root.mainloop()
    except Exception as e:  # noqa: BLE001
        logger.debug("captcha flash failed: %s", e)


class _Pointer:
    def __init__(self):
        self.gui = _gui()
        self.width, self.height = self.gui.size()

    def move(self, x, y, duration=None):
        x = min(max(int(x), 1), self.width - 2)  # the corners trigger pyautogui's fail-safe
        y = min(max(int(y), 1), self.height - 2)
        if duration is None:
            duration = random.uniform(1.2, 2.2)
        self.gui.moveTo(x, y, duration=duration, tween=self.gui.easeInOutQuad)
        _flash(x, y)
        return x, y

    def wander(self, moves=WANDER_MOVES):
        """A few quick moves, under 2 s in all."""
        for _ in range(moves):
            self.move(*random_point(self.width, self.height), duration=random.uniform(0.4, 0.7))
            _pause(0.1, 0.25)

    def click(self, x, y):
        self.move(x, y)
        _pause(0.2, 0.6)
        self.gui.click()


def _pause(low, high):
    context.check_cancelled()
    time.sleep(random.uniform(low, high))


# the page ---------------------------------------------------------------------------------------


def _evaluate(page, script, default=None):
    """evaluate() that survives the navigation a solved challenge triggers."""
    try:
        return page.evaluate(script)
    except Exception as e:  # noqa: BLE001
        logger.debug("captcha: evaluate failed: %s", e)
        return default


def challenged(page) -> bool | None:
    """True or False, or None when the page cannot tell (it is navigating, as it does once a challenge clears)."""
    if any(CHALLENGE_FRAME in (frame.url or "") for frame in _frames(page)):
        return True
    state = _evaluate(page, _STATE_JS)
    return None if not isinstance(state, dict) else bool(state.get("challenge"))


def _frames(page):
    try:
        return list(page.frames)
    except Exception:  # noqa: BLE001
        return []


def _watch(page, seconds) -> bool | None:
    """Looks for a challenge for up to `seconds`, since one can show up after the page has opened; the state
    once one shows, or at the end."""
    deadline = time.monotonic() + seconds
    while True:
        state = challenged(page)
        if state or time.monotonic() >= deadline:
            return state
        _pause(0.8, 1.2)


def _cleared(page) -> bool:
    """The challenge is gone: two definite answers in a row, then the page it led to has loaded."""
    if challenged(page) is not False:
        return False
    _pause(0.8, 1.2)
    if challenged(page) is not False:
        return False
    try:
        page.wait_for_load_state("load", timeout=15000)
    except Exception:  # noqa: BLE001
        pass
    return True


def _widget_box(page):
    for frame in _frames(page):
        if CHALLENGE_FRAME in (frame.url or ""):
            try:
                box = frame.frame_element().bounding_box()
            except Exception:  # noqa: BLE001
                box = None
            if box and box["width"] > 20 and box["height"] > 20:
                return box
    return _evaluate(page, _WIDGET_BOX_JS)


def _wait_widget(page, timeout):
    """(box, cleared): the widget box once it is rendered; cleared when the challenge passed on its own.
    A box of None and not cleared: no widget showed in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        box = _widget_box(page) if challenged(page) else None
        if box:
            return box, False
        if challenged(page) is False and _cleared(page):
            return None, True
        _pause(0.8, 1.2)
    return None, False


def _screen_map(page, pointer: _Pointer) -> ScreenMap:
    """Moves the pointer to a blank spot of the viewport (the lower middle) and maps by where the page saw it."""
    geometry = _evaluate(page, _GEOMETRY_JS)
    if not geometry:
        raise RuntimeError("captcha: cannot read the window geometry")
    estimate = ScreenMap.estimate(geometry)
    _evaluate(page, _TRACK_JS)
    page_x = geometry["innerWidth"] * random.uniform(0.4, 0.6)
    page_y = geometry["innerHeight"] * random.uniform(0.75, 0.85)
    screen_point = pointer.move(*estimate.to_screen(page_x, page_y))
    _pause(0.3, 0.6)
    seen = _evaluate(page, _POINTER_JS)
    if seen:
        return ScreenMap.calibrated(screen_point, seen, estimate.scale)
    logger.info("captcha: the page did not see the pointer, using the window geometry")
    return estimate


def _wait_clear(page, timeout=CLEAR_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _pause(0.8, 1.2)
        if _cleared(page):
            return True
    return False


def solve(page, headless: bool, timeout: float = 60, attempts: int = 2, watch: float = 0,
          expected: bool = False) -> bool:
    """Passes a Cloudflare challenge on `page`, if there is one. True when the page is through.

    `watch`: seconds to look for a challenge that has not shown yet. `expected`: the page's navigation was
    answered with `cf-mitigated: challenge`, so there is one even before the page shows it."""
    state = _watch(page, watch)
    if not state and not expected:
        if state is None:
            logger.info("captcha: cannot read the challenge state of %s, taking it as through", page.url)
        return True
    if headless:
        raise RuntimeError("solving a captcha needs a headful browser (browser-headless, browser-display)")
    with _lock:
        pointer = _Pointer()
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        deadline = time.monotonic() + timeout
        for attempt in range(1, attempts + 1):
            box, cleared = _wait_widget(page, max(1, deadline - time.monotonic()))
            if cleared:
                logger.info("captcha: the challenge cleared")
                return True
            pointer.wander()
            if box:
                target = _screen_map(page, pointer).to_screen(*checkbox_point(box))
            elif conf.get("captcha-position"):
                x, y = conf.get("captcha-position")
                target = (int(x) + random.randint(1, 3), int(y) + random.randint(1, 3))
                logger.info("captcha: no widget found, clicking captcha-position")
            else:
                logger.warning("captcha: no Turnstile widget found on %s", page.url)
                return False
            logger.info("captcha: attempt %d, clicking at %s", attempt, target)
            pointer.click(*target)
            if _wait_clear(page, min(CLEAR_TIMEOUT, max(1, deadline - time.monotonic()))):
                logger.info("captcha: solved")
                return True
            if time.monotonic() >= deadline:
                break
        logger.warning("captcha: still challenged on %s", page.url)
        return False
