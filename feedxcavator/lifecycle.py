"""Process lifecycle: restarts, child process tracking, and recovery after a forced kill."""

from __future__ import annotations

import os
import threading
import time

import psutil

from feedxcavator import conf, storage

RESTART_EXIT_CODE = 3
CHILDREN_POLL_SECONDS = 10


def children_file():
    return conf.DATA_DIR / "children.pid"


def _key(process: psutil.Process) -> str | None:
    """A PID and its start time identify a process, since PIDs are reused."""
    try:
        return f"{process.pid} {int(process.create_time() * 1000)}"
    except psutil.Error:
        return None


def _descendants() -> list[psutil.Process]:
    try:
        return psutil.Process().children(recursive=True)
    except psutil.Error:
        return []


def stop_orphaned_children():
    """Kills the processes recorded by an instance that did not shut down cleanly."""
    path = children_file()
    if not path.exists():
        return
    for line in path.read_text(encoding="ascii", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            process = psutil.Process(int(parts[0]))
            if _key(process) != line.strip() or process.pid == os.getpid():
                continue
            print(f"stopping a process left by the previous instance, pid: {process.pid}", flush=True)
            for child in reversed(process.children(recursive=True)):
                _kill(child)
            _kill(process)
        except (psutil.Error, ValueError):
            continue
    path.unlink(missing_ok=True)


def _kill(process: psutil.Process):
    try:
        process.kill()
    except psutil.Error:
        pass


def delete_stale_temp_files():
    """Temporary payload files older than this process are leftovers of a killed write."""
    started = psutil.Process().create_time()
    for root in (conf.FILES_DIR,):
        if not root.is_dir():
            continue
        for path in root.rglob("*.tmp"):
            try:
                if storage.TMP_SUFFIX.search(path.name) and path.stat().st_mtime < started:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


def recover_after_unclean_exit():
    try:
        stop_orphaned_children()
    except Exception as e:  # noqa: BLE001
        print(f"failed to stop orphaned child processes: {e}", flush=True)
    threading.Thread(target=_safe, args=(delete_stale_temp_files,), daemon=True, name="temp-cleanup").start()


def _safe(fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        print(f"{fn.__name__} failed: {e}", flush=True)


class ChildTracker:
    """Records the live descendants every 10 seconds, rewriting the file only when the set changes."""

    def __init__(self):
        self._recorded = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="child-process-tracker")

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self.record()
            self._stop.wait(CHILDREN_POLL_SECONDS)

    def record(self):
        try:
            keys = {k for k in (_key(p) for p in _descendants()) if k}
            if keys != self._recorded:
                path = children_file()
                if keys:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("\n".join(sorted(keys)) + "\n", encoding="ascii")
                else:
                    path.unlink(missing_ok=True)
                self._recorded = keys
        except Exception as e:  # noqa: BLE001
            print(f"failed to record child processes: {e}", flush=True)

    def stop(self):
        self._stop.set()


def stop_child_processes(timeout: float = 5):
    """On shutdown, terminates every child process and forgets them."""
    children = _descendants()
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(children, timeout=timeout)
    for child in alive:
        _kill(child)
    try:
        children_file().unlink(missing_ok=True)
    except OSError:
        pass


def request_restart(delay: float = 0.5):
    """Exits with the restart code shortly, so the current response is delivered."""

    def exit_later():
        time.sleep(delay)
        os._exit(RESTART_EXIT_CODE)

    threading.Thread(target=exit_later, daemon=True, name="restart").start()
