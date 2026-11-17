"""A stable Camoufox identity per browser profile.

Without options Camoufox generates a new fingerprint on every launch, with a random OS. A profile signed in
under one identity is then opened under another, and an identity that claims another OS than the host's
fails checks such as Reddit's sign-in ("user interaction failed"). The fingerprint is generated once for
the host's OS and kept in the profile folder, so `firefox-profile.sh` and the worker present the same one.

An identity on the host's OS claims the host's GPU, which Camoufox reads by starting a bare browser before
each launch (a short extra window). The GPU it read is kept in the same file, and later launches of the
profile are given it instead of starting that browser. Normally it is read once, by `firefox-profile.sh`.

This module imports nothing from the server, so `firefox-profile.sh` can use it without a configuration.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

logger = logging.getLogger("feedxcavator.browser")

FINGERPRINT_FILE = "feedxcavator-fingerprint.json"

_local = threading.local()  # the profile file of the launch running on this thread
_install_lock = threading.Lock()
_installed = False


def host_os() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _read(path: Path) -> dict:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        return saved if isinstance(saved, dict) and saved.get("os") == host_os() else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("ignoring the saved camoufox identity %s: %s", path, e)
        return {}


def _write(path: Path, saved: dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved), encoding="utf-8")
    tmp.replace(path)


def _install_gpu_cache():
    """Route Camoufox's host GPU probe through the profile file of the launch on this thread."""
    global _installed
    with _install_lock:
        if _installed:
            return
        try:
            import camoufox.utils as cu

            original = cu.host_gpu
        except (ImportError, AttributeError):  # a camoufox without the probe
            _installed = True
            return

        def host_gpu(*args, **kwargs):
            path = getattr(_local, "path", None)
            if path is None:
                return original(*args, **kwargs)
            saved = _read(path)
            if "host_gpu" in saved:
                gpu = saved["host_gpu"]
                return tuple(gpu) if gpu else None
            gpu = original(*args, **kwargs)
            if saved.get("fingerprint") is not None:
                saved["host_gpu"] = list(gpu) if gpu else None
                try:
                    _write(path, saved)
                except OSError as e:
                    logger.warning("cannot save the camoufox host GPU to %s: %s", path, e)
            return gpu

        cu.host_gpu = host_gpu
        _installed = True


def launch_options(profile_dir: str | Path) -> dict:
    """Options for `Camoufox(...)`: the host's OS and the fingerprint saved in the profile folder (created
    on the first launch). Delete the file to get a new identity."""
    target_os = host_os()
    options: dict = {"os": target_os}
    path = Path(profile_dir) / FINGERPRINT_FILE
    saved = _read(path)
    fingerprint = saved.get("fingerprint") if isinstance(saved.get("fingerprint"), dict) else None
    if fingerprint is None:
        try:
            from camoufox.fingerprints import generate_fingerprint

            fingerprint = generate_fingerprint(os=target_os)
            _write(path, {"os": target_os, "fingerprint": fingerprint})
        except Exception as e:  # noqa: BLE001 - an older camoufox, or a fingerprint that is not JSON
            logger.warning("cannot keep a camoufox fingerprint for %s (%s); only the OS is fixed", profile_dir, e)
            return options
    options["fingerprint"] = fingerprint
    return options


def browser_env(profile_dir: str | Path, environ: dict | None = None) -> dict | None:
    """Linux: the environment for the browser, with HOME set to the profile folder. Firefox on Linux keeps an
    application folder in HOME (~/.camoufox) even when it is given a profile, and Camoufox creates it before
    each launch; with this it is created inside the profile folder instead. The XDG folders and the X
    authority file still point to the real home, so fonts, caches and display access stay as they were.
    None on other systems."""
    if not sys.platform.startswith("linux"):
        return None
    env = dict(os.environ if environ is None else environ)
    home = Path(env.get("HOME") or Path.home())
    for name, default in (("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"),
                          ("XDG_DATA_HOME", ".local/share"), ("XDG_STATE_HOME", ".local/state")):
        env.setdefault(name, str(home / default))
    xauthority = home / ".Xauthority"
    if "XAUTHORITY" not in env and xauthority.exists():
        env["XAUTHORITY"] = str(xauthority)
    env["HOME"] = str(Path(profile_dir).resolve())
    return env


def start(profile_dir: str | Path, headless: bool, **extra):
    """Start a persistent Camoufox on the profile, with its saved identity. Returns (manager, context);
    stop it with `manager.__exit__(None, None, None)`."""
    from camoufox.sync_api import Camoufox

    if "env" not in extra:
        env = browser_env(profile_dir)
        if env is not None:
            extra["env"] = env
    _install_gpu_cache()
    manager = Camoufox(persistent_context=True, user_data_dir=str(profile_dir), headless=headless,
                       **launch_options(profile_dir), **extra)
    _local.path = Path(profile_dir) / FINGERPRINT_FILE
    try:
        context = manager.__enter__()
    finally:
        _local.path = None
    return manager, context
