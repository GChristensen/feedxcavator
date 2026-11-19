"""The geometry of solve-captcha (the pointer and the browser are not exercised)."""

from feedxcavator import captcha
from feedxcavator.captcha import ScreenMap


def test_random_point_keeps_the_margin():
    for _ in range(500):
        x, y = captcha.random_point(1920, 1080)
        assert 288 <= x <= 1632
        assert 162 <= y <= 918


def test_estimate_from_window_geometry():
    geometry = {"screenX": 100, "screenY": 50, "outerWidth": 1216, "outerHeight": 900,
                "innerWidth": 1200, "innerHeight": 780, "dpr": 1}
    m = ScreenMap.estimate(geometry)
    # 8px side borders, the rest of the height (112px) above the viewport
    assert m.to_screen(0, 0) == (108, 162)
    assert m.to_screen(10, 20) == (118, 182)


def test_estimate_scales_with_device_pixel_ratio():
    geometry = {"screenX": 100, "screenY": 50, "outerWidth": 1200, "outerHeight": 900,
                "innerWidth": 1200, "innerHeight": 800, "dpr": 1.5}
    assert ScreenMap.estimate(geometry).to_screen(10, 10) == (165, 240)


def test_calibration_maps_the_seen_point_back():
    m = ScreenMap.calibrated((700, 900), (600, 650), 1.25)
    assert m.to_screen(600, 650) == (700, 900)
    assert m.to_screen(0, 0) == (-50, 88)


def test_checkbox_point_is_near_the_left_of_the_widget():
    box = {"x": 400, "y": 300, "width": 300, "height": 65}
    for _ in range(100):
        x, y = captcha.checkbox_point(box)
        assert 427 <= x <= 433
        assert 330 <= y <= 335
    # a narrow box: the middle
    x, _ = captcha.checkbox_point({"x": 0, "y": 0, "width": 40, "height": 40})
    assert 18 <= x <= 22


class _FakePage:
    """Answers the challenge check with `states` in turn (the last one repeats); None: evaluate fails."""

    frames = []

    def __init__(self, states):
        self.states = list(states)
        self.url = "https://example.com/"

    def evaluate(self, script):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if state is None:
            raise RuntimeError("Execution context was destroyed")
        return {"challenge": state}

    def wait_for_load_state(self, *args, **kwargs):
        pass


def _fast(monkeypatch):
    monkeypatch.setattr(captcha, "_pause", lambda low, high: None)


def test_solve_without_a_challenge_does_not_touch_the_pointer(monkeypatch):
    monkeypatch.setattr(captcha, "_Pointer", lambda: (_ for _ in ()).throw(AssertionError("pointer used")))
    assert captcha.solve(_FakePage([False]), headless=True) is True


def test_solve_in_headless_mode_raises():
    import pytest

    with pytest.raises(RuntimeError, match="headful"):
        captcha.solve(_FakePage([True]), headless=True)


def test_watch_finds_a_challenge_that_shows_up_later(monkeypatch):
    import pytest

    _fast(monkeypatch)
    page = _FakePage([False, False, False, True])
    with pytest.raises(RuntimeError, match="headful"):
        captcha.solve(page, headless=True, watch=5)


def test_a_failed_check_is_not_taken_as_cleared(monkeypatch):
    _fast(monkeypatch)
    assert captcha.challenged(_FakePage([None])) is None
    # cleared needs two definite False answers in a row; a failed check (None) does not count
    assert captcha._cleared(_FakePage([False, True])) is False
    assert captcha._cleared(_FakePage([None])) is False
    assert captcha._cleared(_FakePage([False, False])) is True


def test_a_challenge_frame_counts_without_the_markup():
    page = _FakePage([False])
    page.frames = [type("F", (), {"url": "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/x"})()]
    assert captcha.challenged(page) is True


def test_an_expected_challenge_is_solved_even_before_it_shows(monkeypatch):
    import pytest

    _fast(monkeypatch)
    with pytest.raises(RuntimeError, match="headful"):
        captcha.solve(_FakePage([False]), headless=True, expected=True)


def test_xauthority_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(captcha.sys, "platform", "linux")
    env = {"HOME": str(tmp_path)}
    captcha._xauthority_fallback(env)
    assert env["XAUTHORITY"] == captcha.os.devnull
    (tmp_path / ".Xauthority").write_bytes(b"")
    env = {"HOME": str(tmp_path)}
    captcha._xauthority_fallback(env)
    assert "XAUTHORITY" not in env
    env = {"HOME": str(tmp_path / "none"), "XAUTHORITY": "/run/x/auth"}
    captcha._xauthority_fallback(env)
    assert env["XAUTHORITY"] == "/run/x/auth"
