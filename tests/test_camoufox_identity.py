from pathlib import Path

from feedxcavator import camoufox_identity


def test_browser_env_moves_home_to_the_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(camoufox_identity.sys, "platform", "linux")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".Xauthority").write_text("")
    profile = tmp_path / "browser" / "twitter"

    env = camoufox_identity.browser_env(profile, {"HOME": str(home), "XDG_CACHE_HOME": "/var/cache/me",
                                                 "DISPLAY": ":0"})

    assert env["HOME"] == str(profile.resolve())
    assert env["XDG_CONFIG_HOME"] == str(home / ".config")
    assert env["XDG_DATA_HOME"] == str(home / ".local/share")
    assert env["XDG_CACHE_HOME"] == "/var/cache/me"  # a set value is kept
    assert env["XAUTHORITY"] == str(home / ".Xauthority")
    assert env["DISPLAY"] == ":0"


def test_browser_env_keeps_a_set_xauthority(tmp_path, monkeypatch):
    monkeypatch.setattr(camoufox_identity.sys, "platform", "linux")
    env = camoufox_identity.browser_env(tmp_path / "p", {"HOME": str(tmp_path), "XAUTHORITY": "/run/x/auth"})
    assert env["XAUTHORITY"] == "/run/x/auth"


def test_browser_env_only_on_linux(tmp_path, monkeypatch):
    for platform in ("win32", "darwin"):
        monkeypatch.setattr(camoufox_identity.sys, "platform", platform)
        assert camoufox_identity.browser_env(Path(tmp_path), {"HOME": str(tmp_path)}) is None
