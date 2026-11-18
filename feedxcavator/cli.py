"""Command line entry point and the supervisor that runs the web and worker processes."""

from __future__ import annotations

import argparse
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time

from feedxcavator import VERSION

COMMANDS = {
    "set-password": "setpassword",
    "import-backup": "importbackup",
    "telegram-login": "telegramlogin",
}


def _setup():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "feedxcavator.settings")
    import django

    django.setup()


def _migrate():
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)


def _log_writer():
    """log/app.log, rotated at 5 MB with one backup. The supervisor is its only writer."""
    from feedxcavator import conf

    conf.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(conf.LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024,
                                                   backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("feedxcavator.serverlog")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


def _pump(process: subprocess.Popen, name: str, logger):
    for raw in iter(process.stdout.readline, b""):
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        try:
            print(line, flush=True)
        except (OSError, UnicodeEncodeError):
            pass
        logger.info("[%s] %s", name, line)


def run_supervisor(dev: bool = False) -> int:
    from feedxcavator import conf, lifecycle

    lifecycle.recover_after_unclean_exit()
    _setup()
    _migrate()
    logger = _log_writer()
    tracker = lifecycle.ChildTracker().start()
    stopping = threading.Event()
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    processes: dict[str, subprocess.Popen] = {}

    def start(name: str):
        args = [sys.executable, "-m", "feedxcavator", name] + (["--dev"] if dev and name == "web" else [])
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(args, cwd=str(conf.HOME), env=env, stdout=subprocess.PIPE,  # noqa: S603
                                   stderr=subprocess.STDOUT, creationflags=flags)
        processes[name] = process
        threading.Thread(target=_pump, args=(process, name, logger), daemon=True, name=f"log-{name}").start()
        tracker.record()

    def stop(*_):
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)

    for name in ("worker", "web"):
        start(name)
    host = conf.get("host") or "0.0.0.0"  # noqa: S104
    line = f"Feedxcavator started on http {'*' if host in ('0.0.0.0', '::') else host}:{conf.get('port')}"  # noqa: S104
    print(line, flush=True)  # one line with the active listeners
    logger.info(line)

    exit_code = 0
    restarts: dict[str, list[float]] = {"web": [], "worker": []}
    while not stopping.is_set():
        for name, process in list(processes.items()):
            code = process.poll()
            if code is None:
                continue
            if code == lifecycle.RESTART_EXIT_CODE:  # a restart requested from the console
                exit_code = lifecycle.RESTART_EXIT_CODE
                stopping.set()
                break
            now = time.monotonic()
            restarts[name] = [t for t in restarts[name] if now - t < 60] + [now]
            if len(restarts[name]) > 5:
                print(f"the {name} process keeps exiting (code {code}), giving up", file=sys.stderr, flush=True)
                exit_code = 1
                stopping.set()
                break
            print(f"the {name} process exited with code {code}, starting it again", file=sys.stderr, flush=True)
            time.sleep(1)
            start(name)
        stopping.wait(0.5)

    tracker.stop()
    for process in processes.values():  # stop the children on shutdown
        if process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.terminate()
            except OSError:
                pass
    deadline = time.monotonic() + 8
    for process in processes.values():
        try:
            process.wait(max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    lifecycle.stop_child_processes(timeout=3)
    return exit_code


def run_web(dev: bool = False):
    import uvicorn

    from feedxcavator import conf

    uvicorn.run(
        "feedxcavator.asgi:application",
        host=conf.get("host") or "0.0.0.0",  # noqa: S104
        port=int(conf.get("port")),
        server_header=False,  # no Server version header
        date_header=True,
        timeout_keep_alive=60,  # a stalled client does not hold a connection forever
        lifespan="off",
        log_level="info" if dev else "warning",
        access_log=dev,
        reload=dev,  # code reloading with --dev
        reload_dirs=[os.path.dirname(os.path.abspath(__file__))] if dev else None,
    )


def run_worker():
    from feedxcavator import worker

    worker.main()


def manage(argv: list[str]):
    _setup()
    from django.core.management import execute_from_command_line

    execute_from_command_line(["feedxcavator", *argv])


def main(argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="feedxcavator", description=f"Feedxcavator {VERSION}")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--dev", action="store_true", help="reload the web process when its code changes")
    parser.add_argument("command", nargs="?", default="run",
                        help="run (default), web, worker, migrate, set-password, import-backup, telegram-login, "
                             "or manage <django command>")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    dev = options.dev or "--dev" in options.args
    rest = [a for a in options.args if a != "--dev"]

    command = options.command
    if command == "run":
        sys.exit(run_supervisor(dev))
    if command == "web":
        _setup()
        _migrate()
        return run_web(dev)
    if command == "worker":
        _setup()
        _migrate()
        return run_worker()
    if command == "migrate":
        _setup()
        return _migrate()
    if command == "manage":
        return manage(rest)
    if command in COMMANDS:
        return manage([COMMANDS[command], *rest])
    parser.error(f"unknown command: {command}")


if __name__ == "__main__":
    main()
