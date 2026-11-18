"""The application log, stored in LogEntry."""

from __future__ import annotations

import logging
import pprint
import sys
import threading
import time
import traceback

from feedxcavator import context

LEVELS = ("info", "warning", "error")
_logger = logging.getLogger("feedxcavator")


def _level_name(level) -> str:
    name = getattr(level, "name", None) or str(level)
    name = name.lstrip(":").lower()
    return name if name in LEVELS else "info"


def format_message(message) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, BaseException):
        return "".join(traceback.format_exception(type(message), message, message.__traceback__))
    return pprint.pformat(message)


def write(*args):
    """write(message) or write(level, message). Level is `info` (default), `warning` or `error`.

    A string is stored as is, an exception as its traceback, any other value pretty-printed."""
    if len(args) >= 2:
        level, message = _level_name(args[0]), args[1]
    elif args:
        level, message = "info", args[0]
    else:
        return
    text = format_message(message)
    source = context.logging_source.get() or ""
    try:
        from feedxcavator.models import LogEntry

        LogEntry.objects.create(
            level=level,
            source=source,
            timestamp=int(time.time() * 1000),
            message=text,
            task_run_id=context.task_run_id.get(),
        )
    except Exception as e:  # the log must never break the caller
        print(f"failed to write a log entry: {e}\n[{level}] {source}: {text}", file=sys.__stderr__)
    _logger.log(
        {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}[level],
        "[%s] %s", source or "-", text if len(text) < 2000 else text[:2000] + "...",
    )


def read_entries(n: int, from_: int | None = None) -> list[dict]:
    """Newest first. With `from_`, entries numbered from `from_` down."""
    from feedxcavator.models import LogEntry

    query = LogEntry.objects.order_by("-id")
    if from_ is not None:
        query = query.filter(id__lte=from_)
    return [
        {"number": e.id, "level": e.level, "source": e.source, "timestamp": e.timestamp, "message": e.message}
        for e in query[: max(0, n)]
    ]


def clear():
    from feedxcavator.models import LogEntry

    LogEntry.objects.all().delete()


def install_excepthooks():
    """Uncaught exceptions on any thread go to stderr and the application log."""

    def hook(exc_type, exc, tb):
        traceback.print_exception(exc_type, exc, tb, file=sys.__stderr__)
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            write("error", exc)

    def thread_hook(args):
        hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = hook
    threading.excepthook = thread_hook


class ContextStdout:
    """A stdout that diverts writes into a per-context buffer when one is set, for scratch runs."""

    def __init__(self, original):
        self._original = original

    def write(self, text):
        buffer = context.stdout_capture.get()
        if buffer is not None:
            return buffer.write(text)
        return self._original.write(text)

    def flush(self):
        if context.stdout_capture.get() is None:
            self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


def install_stdout():
    if not isinstance(sys.stdout, ContextStdout):
        sys.stdout = ContextStdout(sys.stdout)
