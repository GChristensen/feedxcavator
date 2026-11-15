"""Configuration: `config.yml` in the home directory plus environment overrides (CFG-2, CFG-3, CFG2-*)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import yaml

HOME = Path(os.environ.get("FEEDXCAVATOR_HOME") or os.getcwd()).resolve()

DEFAULTS = {
    "port": 10000,
    "host": "0.0.0.0",
    "stacktraces": True,
    "repl-port": None,
    "hostname": None,
    "websub-hostname": None,
    "secure-hostname": None,
    "authentication": True,
    "user": None,
    "websub": True,
    "database-connection-url": "sqlite:///data/feedxcavator.db",
    "execute-daily-service-task": False,
    "production": False,
    "tg-api-id": None,
    "tg-api-hash": None,
    # CFG2-4
    "workspace": "workspace",
    "x-accel": None,
    "worker-parallel": 6,
    "worker-sequential": 1,
    "browser-pool-size": 1,
    "browser-headless": None,  # None: headful when a display (or Xvfb) is available, else headless
    "browser-engine": "patchright",  # patchright | camoufox | playwright
    "browser-channel": "chrome",
    "run-history-days": 30,
}

# keys of 3.0.0 that no longer have a meaning (CFG2-1, CFG2-3, AUTH2-1)
REMOVED = {
    "http", "ssl-port", "keystore", "keystore-password", "keystore-type", "keystore-reload-interval",
    "sni-host-check", "nrepl-port", "exclusive-jdbc-connection", "auto-backup-database", "password-hash",
    "tdlib-path",
}

ENV = {
    "FEEDXCAVATOR_PORT": ("port", int),
    "FEEDXCAVATOR_HOST": ("host", str),
    "FEEDXCAVATOR_REPL_PORT": ("repl-port", int),
}


def load(path: Path | None = None) -> dict:
    path = path or HOME / "config.yml"
    data = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: the configuration must be a mapping")
    conf = dict(DEFAULTS)
    for key, value in data.items():
        key = str(key)
        if key in REMOVED:
            continue
        if key == "database-connection-url" and str(value).startswith("jdbc:"):
            continue  # a 3.0.0 JDBC URL, fall back to the default database
        conf[key] = value
    for name, (key, cast) in ENV.items():
        if os.environ.get(name):
            conf[key] = cast(os.environ[name])
    return conf


config = load()


def get(key: str, default=None):
    value = config.get(key)
    return default if value is None else value


DATA_DIR = HOME / "data"
LOG_DIR = HOME / "log"
FILES_DIR = DATA_DIR / "files"


def workspace_dir() -> Path:
    path = Path(str(get("workspace", "workspace")))
    return path if path.is_absolute() else HOME / path


def supervised() -> bool:
    return os.environ.get("FEEDXCAVATOR_SUPERVISED") == "1"


def secret_key() -> str:
    """The Django secret key, generated once and kept in the data directory."""
    path = DATA_DIR / "secret.key"
    if path.exists():
        return path.read_text(encoding="ascii").strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    try:
        with open(path, "x", encoding="ascii") as f:
            f.write(key)
    except FileExistsError:  # the other process was faster
        return path.read_text(encoding="ascii").strip()
    return key


def app_host() -> str | None:
    return get("hostname")


def websub_host() -> str:
    return get("websub-hostname") or get("hostname") or f"http://localhost:{get('port')}"


def secure_host() -> str | None:
    return get("secure-hostname") or get("hostname")
