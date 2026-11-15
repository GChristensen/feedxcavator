"""Ambient per-request and per-job state, held in contextvars (EX-5, FT2-1)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

current_feed: ContextVar = ContextVar("current_feed", default=None)
logging_source: ContextVar = ContextVar("logging_source", default=None)
request_host: ContextVar = ContextVar("request_host", default=None)
run_mode: ContextVar = ContextVar("run_mode", default=None)  # request | background | test
task_run_id: ContextVar = ContextVar("task_run_id", default=None)
cancel_event: ContextVar = ContextVar("cancel_event", default=None)
log_fetch_errors: ContextVar = ContextVar("log_fetch_errors", default=False)
stdout_capture: ContextVar = ContextVar("stdout_capture", default=None)
_fetch_state: ContextVar = ContextVar("fetch_state")


class JobCancelled(Exception):
    """Raised inside a feed job whose task run has timed out (TSK2-7)."""


class FetchState:
    __slots__ = ("response", "http_error", "network_error", "conversion_error")

    def __init__(self):
        self.reset()

    def reset(self):
        self.response = None
        self.http_error = None
        self.network_error = None
        self.conversion_error = None


def fetch_state() -> FetchState:
    try:
        return _fetch_state.get()
    except LookupError:
        state = FetchState()
        _fetch_state.set(state)
        return state


def new_fetch_state() -> FetchState:
    state = FetchState()
    _fetch_state.set(state)
    return state


def cancelled() -> bool:
    event = cancel_event.get()
    return bool(event is not None and event.is_set())


def check_cancelled():
    if cancelled():
        raise JobCancelled("the task run timed out")


_VARS = {
    "feed": current_feed,
    "source": logging_source,
    "mode": run_mode,
    "task_run": task_run_id,
    "cancel": cancel_event,
    "log_fetch": log_fetch_errors,
    "host": request_host,
}


@contextmanager
def scope(**values):
    """Sets the named context variables for the duration of the block."""
    tokens = [(_VARS[name], _VARS[name].set(value)) for name, value in values.items()]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
