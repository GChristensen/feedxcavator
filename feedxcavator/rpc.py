"""A local RPC channel from the web process to the worker, for the browser pool and Telegram (PRC-4, TG-4).

The worker listens on 127.0.0.1 and writes its port and key to data/worker.rpc.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from multiprocessing.connection import Client, Listener

from feedxcavator import conf

logger = logging.getLogger("feedxcavator.rpc")


def _info_path():
    return conf.DATA_DIR / "worker.rpc"


def connect():
    path = _info_path()
    if not path.exists():
        raise OSError("the worker is not running")
    info = json.loads(path.read_text(encoding="utf-8"))
    return Client(("127.0.0.1", info["port"]), authkey=bytes.fromhex(info["key"]))


def call(method: str, *args, **kwargs):
    """One request on a fresh connection."""
    connection = connect()
    try:
        connection.send((method, args, kwargs))
        status, value = connection.recv()
    finally:
        connection.close()
    if status != "ok":
        raise RuntimeError(value)
    return value


def worker_alive() -> bool:
    try:
        return call("ping") == "pong"
    except Exception:  # noqa: BLE001
        return False


class Server:
    def __init__(self):
        self._key = secrets.token_bytes(32)
        self._listener = Listener(("127.0.0.1", 0), authkey=self._key)
        conf.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _info_path().write_text(
            json.dumps({"port": self._listener.address[1], "key": self._key.hex(), "pid": os.getpid()}),
            encoding="utf-8",
        )
        self._thread = threading.Thread(target=self._accept, daemon=True, name="rpc-accept")
        self._thread.start()

    def _accept(self):
        while True:
            try:
                connection = self._listener.accept()
            except Exception as e:  # noqa: BLE001 - failed authentication, closed listener
                if getattr(self._listener, "_listener", None) is None:
                    return
                logger.debug("rpc accept failed: %s", e)
                continue
            threading.Thread(target=self._serve, args=(connection,), daemon=True, name="rpc-client").start()

    def _serve(self, connection):
        from django.db import connection as db_connection

        from feedxcavator import browser, context

        session = None
        try:
            while True:
                try:
                    method, args, kwargs = connection.recv()
                except (EOFError, OSError):
                    break
                try:
                    if method == "ping":
                        result = "pong"
                    elif method == "browser_open":
                        if browser._pool is None:
                            raise RuntimeError("the worker has no browser pool")
                        session = browser._pool.acquire(*args)
                        result = True
                    elif method == "browser_call":
                        if session is None:
                            raise RuntimeError("no browser session is open on this connection")
                        op, op_args, op_kwargs = args
                        result = session.call(op, *op_args, **op_kwargs)
                    elif method == "browser_close":
                        if session is not None:
                            browser._pool.release(session)
                            session = None
                        result = True
                    elif method in ("telegram_get_channel_messages", "telegram_notify"):
                        from feedxcavator import telegram

                        context.new_fetch_state()
                        result = getattr(telegram, "_local_" + method.removeprefix("telegram_"))(*args, **kwargs)
                    else:
                        raise RuntimeError(f"unknown method {method}")
                    connection.send(("ok", result))
                except Exception as e:  # noqa: BLE001
                    logger.warning("rpc %s failed: %s", method, e)
                    try:
                        connection.send(("err", f"{type(e).__name__}: {e}"))
                    except (OSError, ValueError):
                        break
        finally:
            if session is not None:  # the client went away without closing its browser
                try:
                    browser._pool.release(session)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to release a browser session")
            try:
                connection.close()
            except OSError:
                pass
            db_connection.close()

    def close(self):
        try:
            self._listener.close()
        finally:
            _info_path().unlink(missing_ok=True)
