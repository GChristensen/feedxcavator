"""The worker process: scheduler, task queue, browser pool, Telegram (PRC-1, PRC-3)."""

from __future__ import annotations

import code
import logging
import random
import signal
import socket
import threading
import time
from datetime import datetime, timedelta

from django.db import connection
from django.utils import timezone

from feedxcavator import applog, conf, context, maintenance, workspace
from feedxcavator.runner import TaskRunner

logger = logging.getLogger("feedxcavator.worker")
POLL_SECONDS = 1.0


def enqueue(kind: str, **payload):
    """Called by the web process: the request reaches the worker through the database (PRC-3)."""
    from feedxcavator.models import QueueItem

    QueueItem.objects.create(kind=kind, payload=payload, created_at=timezone.now())


def enqueue_task(name: str, trigger: str = "manual"):
    enqueue("task", name=name, trigger=trigger)


class Scheduler:
    """SCH2-*: cron schedules from schedules.yaml, evaluated by a loop in the worker."""

    def __init__(self, fire):
        self._fire = fire  # fire(task_name)
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._first = True
        self._service_next = self._next_service()

    @staticmethod
    def _next_service():
        """MNT-2: daily at 04:00 local time."""
        now = datetime.now().astimezone()
        at = now.replace(hour=4, minute=0, second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)

    def reload(self, registry=None):
        """SCH-6: replaces all timers with the current declarations."""
        from feedxcavator.models import ScheduleState

        registry = registry or workspace.get_registry()
        now = timezone.now()
        entries, misfired = [], []
        for schedule in registry.schedules:
            if not schedule.enabled:
                continue
            state, created = ScheduleState.objects.get_or_create(key=schedule.key, defaults={"first_seen_at": now})
            if self._first and not created and schedule.misfire == "run-once":  # SCH2-3
                since = state.last_fired_at or state.first_seen_at
                if schedule.next_fire(since) <= now:
                    misfired.append(schedule)
            entries.append({"schedule": schedule, "next": schedule.next_fire()})
        self._first = False
        with self._lock:
            self._entries = entries
        for schedule in misfired:
            logger.info("schedule %s missed a firing while the worker was down, running once", schedule.key)
            self._trigger(schedule)

    def _trigger(self, schedule):
        from feedxcavator.models import ScheduleState

        ScheduleState.objects.filter(key=schedule.key).update(last_fired_at=timezone.now())
        delay = random.uniform(0, schedule.jitter) if schedule.jitter else 0  # noqa: S311
        if delay:
            threading.Timer(delay, self._fire, args=(schedule.task,)).start()
        else:
            self._fire(schedule.task)

    def tick(self, now: datetime | None = None):
        now = now or timezone.now()
        due = []
        with self._lock:
            for entry in self._entries:
                if entry["next"] <= now:
                    due.append(entry["schedule"])
                    entry["next"] = entry["schedule"].next_fire(now)
        for schedule in due:
            try:
                self._trigger(schedule)
            except Exception:  # noqa: BLE001
                logger.exception("failed to fire schedule %s", schedule.key)
        if conf.get("execute-daily-service-task") and now >= self._service_next:
            self._service_next = self._next_service()
            threading.Thread(target=_service_task, daemon=True, name="service-task").start()


def _service_task():
    try:
        maintenance.service_task()
    finally:
        connection.close()


class Worker:
    def __init__(self):
        self.runner = TaskRunner()
        self.scheduler = Scheduler(lambda task: self.runner.enqueue(task, "schedule"))
        self._stop = threading.Event()
        self.rpc = None

    def handle(self, item):
        kind, payload = item.kind, item.payload or {}
        if kind == "task":
            self.runner.enqueue(payload["name"], payload.get("trigger") or "manual")
        elif kind == "reschedule":  # SCH-7
            workspace.load(force=True)
            self.scheduler.reload()
        elif kind == "service-task":
            threading.Thread(target=_service_task, daemon=True, name="service-task").start()
        else:
            logger.warning("unknown queue item kind: %s", kind)

    def poll(self):
        from feedxcavator.models import QueueItem

        items = list(QueueItem.objects.order_by("id")[:50])
        if items:
            QueueItem.objects.filter(id__in=[i.id for i in items]).delete()
        for item in items:
            try:
                self.handle(item)
            except Exception:  # noqa: BLE001
                logger.exception("failed to handle a queue item")

    def start(self):
        from feedxcavator import browser, rpc, telegram
        from feedxcavator.models import QueueItem, TaskRun

        applog.install_excepthooks()
        applog.install_stdout()
        telegram.in_worker = True
        # TSK-11: tasks do not survive a restart
        QueueItem.objects.all().delete()
        TaskRun.objects.filter(status="running").update(status="failed", error="interrupted by a restart",
                                                        finished_at=timezone.now())
        workspace.ensure_layout()
        workspace.load(startup=True)
        workspace.add_listener(self.scheduler.reload)
        self.scheduler.reload()
        workspace.start_watcher()
        browser.start_pool()
        self.rpc = rpc.Server()
        if conf.get("repl-port"):
            start_repl(int(conf.get("repl-port")))

    def run_forever(self):
        self.start()
        logger.info("worker started: %d parallel, %d sequential workers", self.runner.parallel._max_workers,
                    self.runner.sequential._max_workers)
        while not self._stop.is_set():
            try:
                self.poll()
                self.scheduler.tick()
            except Exception:  # noqa: BLE001
                logger.exception("worker loop error")
                connection.close()
                time.sleep(2)
            self._stop.wait(POLL_SECONDS)
        self.shutdown()

    def stop(self, *_):
        self._stop.set()

    def shutdown(self):
        from feedxcavator import lifecycle

        if self.rpc is not None:
            self.rpc.close()
        self.runner.shutdown()
        lifecycle.stop_child_processes()  # BRW-6


def start_repl(port: int):
    """CFG2-2: a local Python REPL attached to the worker's live namespace, bound to 127.0.0.1.
    A line starting with `(` is evaluated as Hy."""

    def serve(client: socket.socket):
        stream = client.makefile("rw", encoding="utf-8", newline="\n")

        class Console(code.InteractiveConsole):
            def write(self, data):
                stream.write(data)
                stream.flush()

            def raw_input(self, prompt=""):
                stream.write(prompt)
                stream.flush()
                line = stream.readline()
                if not line:
                    raise EOFError
                return line.rstrip("\r\n")

            def runsource(self, source, filename="<repl>", symbol="single"):
                if source.lstrip().startswith("("):
                    import hy

                    try:
                        module = workspace._new_module(workspace.get_registry().namespace)
                        result = None
                        for form in hy.read_many(source, reader=workspace.hy_reader()):
                            result = hy.eval(form, module=module)
                        if result is not None:
                            self.write(repr(result) + "\n")
                    except Exception:  # noqa: BLE001
                        self.showtraceback()
                    return False
                return super().runsource(source, filename, symbol)

            def runcode(self, code_object):
                import io

                buffer = io.StringIO()
                token = context.stdout_capture.set(buffer)
                try:
                    super().runcode(code_object)
                finally:
                    context.stdout_capture.reset(token)
                    self.write(buffer.getvalue())

        try:
            Console(locals=dict(workspace.get_registry().namespace)).interact(
                banner="Feedxcavator worker REPL. Python, or Hy for lines starting with '('.", exitmsg="")
        except Exception:  # noqa: BLE001, S110
            pass
        finally:
            connection.close()
            client.close()

    def accept():
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(2)
        logger.info("REPL listening on 127.0.0.1:%d", port)
        while True:
            client, _ = server.accept()
            threading.Thread(target=serve, args=(client,), daemon=True, name="repl").start()

    threading.Thread(target=accept, daemon=True, name="repl-accept").start()


def main():
    import django

    django.setup()
    worker = Worker()
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, worker.stop)
    worker.run_forever()
