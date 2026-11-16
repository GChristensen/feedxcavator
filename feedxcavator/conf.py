"""Configuration: `config.yml` in the home directory plus environment overrides (CFG-2, CFG-3, CFG2-*)."""

from __future__ import annotations

import os
import re
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
    "execute-daily-service-task": None,  # 3.0.0 spelling of service-task.enabled
    "service-task": None,  # a mapping, see SERVICE_TASK_DEFAULTS
    "production": False,
    "telegram": None,  # a mapping, see TELEGRAM_DEFAULTS
    "tg-api-id": None,  # 3.0.0 spelling of telegram.api-id
    "tg-api-hash": None,  # 3.0.0 spelling of telegram.api-hash
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


SERVICE_TASK_DEFAULTS = {
    "enabled": False,
    "time": "04:00",  # local time, HH:MM
    "max-age-days": 2,  # images and log entries older than this are deleted
}


TELEGRAM_DEFAULTS = {
    "api-id": None,
    "api-hash": None,
    "default-proxy": False,  # use the proxy named `default` when the calling feed names no fetch.proxy
}


def _section_value(section: str, name: str, defaults: dict, legacy: dict):
    """A key of a mapping in config.yml, else its 3.0.0 top-level spelling, else the default."""
    mapping = get(section) or {}
    if not isinstance(mapping, dict):
        raise ValueError(f"{section}: expected a mapping")
    value = mapping.get(name)
    if value is None and name in legacy:
        value = get(legacy[name])
    return defaults[name] if value is None else value


def service_task(name: str):
    """A key of the `service-task` mapping. `enabled` falls back to `execute-daily-service-task`."""
    return _section_value("service-task", name, SERVICE_TASK_DEFAULTS, {"enabled": "execute-daily-service-task"})


def telegram(name: str):
    """A key of the `telegram` mapping. The API id and hash fall back to `tg-api-id` and `tg-api-hash`."""
    return _section_value("telegram", name, TELEGRAM_DEFAULTS, {"api-id": "tg-api-id", "api-hash": "tg-api-hash"})


def service_task_time() -> tuple[int, int]:
    """`service-task.time` as (hour, minute). Unquoted YAML 1.1 reads 04:00 as the base-60 number 240,
    so an integer is taken as minutes after midnight."""
    value = service_task("time")
    if isinstance(value, int) and not isinstance(value, bool):
        minutes = value
    else:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
        if not match or int(match[2]) > 59:
            raise ValueError(f"service-task.time: expected HH:MM, got {value!r}")
        minutes = int(match[1]) * 60 + int(match[2])
    hour, minute = divmod(minutes, 60)
    if not 0 <= hour <= 23:
        raise ValueError(f"service-task.time: {value!r} is not a time of day")
    return hour, minute


def service_task_max_age_days() -> float:
    value = float(service_task("max-age-days"))
    if value <= 0:
        raise ValueError("service-task.max-age-days: must be positive")
    return value


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
