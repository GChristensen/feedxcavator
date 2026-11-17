"""Running tasks in the worker (PORT-REQUIREMENTS section 11, TSK-6, TSK-7)."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from feedxcavator import applog, conf, context, extraction, workspace

logger = logging.getLogger("feedxcavator.runner")


class TaskContext(dict):
    """What `pre` and `post` functions receive: task, trigger, feeds, and results for `post` (TSK2-6a/b)."""

    __getattr__ = dict.get


class TaskRunner:
    def __init__(self, parallel: int | None = None, sequential: int | None = None):
        self.parallel = ThreadPoolExecutor(int(parallel or conf.get("worker-parallel", 6)), "feed-parallel")
        self.sequential = ThreadPoolExecutor(int(sequential or conf.get("worker-sequential", 1)), "feed-seq")
        self._active: dict[str, int] = {}
        self._queues: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()
        self._threads: set[threading.Thread] = set()

    # entry points -------------------------------------------------------------------------

    def enqueue(self, name: str, trigger: str = "manual") -> threading.Thread:
        """TSK-8: starts a task run and returns immediately."""

        def target():
            try:
                self.run(name, trigger)
            except Exception:  # noqa: BLE001
                logger.exception("task %s failed", name)
            finally:
                connection.close()
                self._threads.discard(threading.current_thread())

        thread = threading.Thread(target=target, daemon=True, name=f"task-{name}")
        self._threads.add(thread)
        thread.start()
        return thread

    def wait_idle(self, timeout: float = 60):
        """Waits for all task runs, including the `then` tasks they start (used by tests)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            threads = [t for t in list(self._threads) if t.is_alive()]
            if not threads:
                return True
            for thread in threads:
                thread.join(max(0.0, deadline - time.monotonic()))
        return False

    # one run ----------------------------------------------------------------------------------

    def run(self, name: str, trigger: str = "manual", parent=None, deadline: float | None = None,
            registry=None):
        """Runs a task to completion: pre, feed jobs and subtasks, post, then (TSK2-6c).
        Returns the TaskRun record."""
        from feedxcavator.models import TaskRun

        registry = registry or workspace.get_registry()
        task = registry.task(name)
        key = task.name.lower()

        def record(status, error=""):
            now = timezone.now()
            return TaskRun.objects.create(task=task.name, trigger=trigger, parent=parent, started_at=now,
                                          finished_at=now, status=status, error=error)

        if task.error:
            with context.scope(source="tasks"):
                applog.write("error", f"Task {task.name} was not run: {task.error}")
            return record("failed", task.error)

        # TSK2-5
        queue_lock = None
        if task.concurrency == "queue":
            with self._lock:
                queue_lock = self._queues.setdefault(key, threading.Lock())
            queue_lock.acquire()
        with self._lock:
            if task.concurrency == "skip" and self._active.get(key):
                skipped = True
            else:
                skipped = False
                self._active[key] = self._active.get(key, 0) + 1
        if skipped:
            return record("skipped", "a run of this task is already active")

        try:
            return self._run(registry, task, trigger, parent, deadline)
        finally:
            with self._lock:
                self._active[key] -= 1
                if not self._active[key]:
                    del self._active[key]
            if queue_lock is not None:
                queue_lock.release()

    def _run(self, registry, task, trigger, parent, deadline):
        from feedxcavator.models import FeedRun, TaskRun

        started = time.monotonic()
        run = TaskRun.objects.create(task=task.name, trigger=trigger, parent=parent, started_at=timezone.now(),
                                     status="running")
        if task.timeout:
            own_deadline = started + task.timeout
            deadline = min(deadline, own_deadline) if deadline else own_deadline
        feeds = registry.task_feeds(task.name)
        own_feeds = registry.own_feeds(task)
        ctx = TaskContext(task=task.name, trigger=trigger, feeds=feeds, results=None, run_id=run.id)

        def finish(status, error=""):
            TaskRun.objects.filter(id=run.id).update(status=status, error=error, finished_at=timezone.now())
            run.status, run.error = status, error
            return run

        def call(fn_name, *args):
            fn = registry.find_task_function(fn_name)
            if fn is None:
                raise LookupError(f"task function '{fn_name}' is not defined")
            context.new_fetch_state()
            with context.scope(source=fn_name, task_run=run.id):
                return fn.fn(*args)

        # pre (TSK2-6a)
        try:
            for fn_name in task.pre:
                if call(fn_name, ctx) is False:
                    return finish("skipped", f"skipped by {fn_name}")
        except Exception as e:  # noqa: BLE001
            with context.scope(source=task.name, task_run=run.id):
                applog.write("error", e)
            return finish("failed", f"pre: {type(e).__name__}: {e}")

        # feed jobs, the `call` function, and subtasks all start together (TSK-4)
        cancel = threading.Event()
        futures = {}
        for feed in own_feeds:
            pool = self.parallel if feed.get("parallel", True) is not False else self.sequential
            futures[pool.submit(self._feed_job, registry, feed, run.id, cancel)] = feed
        call_future = None
        if task.call:
            call_future = self.parallel.submit(self._call_job, call, task.call, ctx)
        sub_results: dict[str, object] = {}
        sub_threads = []
        for sub in task.run:
            def sub_target(sub=sub):
                try:
                    sub_results[sub] = self.run(sub, "subtask", parent=run, deadline=deadline, registry=registry)
                except Exception:  # noqa: BLE001
                    logger.exception("subtask %s failed", sub)
                finally:
                    connection.close()

            thread = threading.Thread(target=sub_target, daemon=True, name=f"task-{sub}")
            thread.start()
            sub_threads.append(thread)

        pending = set(futures) | ({call_future} if call_future else set())
        remaining = (deadline - time.monotonic()) if deadline else None
        done, not_done = wait(pending, timeout=max(0.0, remaining) if remaining is not None else None)
        timed_out = bool(not_done)
        if timed_out:  # TSK2-7
            cancel.set()
            for future in not_done:
                future.cancel()
            now = timezone.now()
            FeedRun.objects.filter(task_run=run, status="running").update(status="timeout", finished_at=now,
                                                                         error="the task run timed out")
            for future in not_done:
                feed = futures.get(future)
                if feed is not None and not FeedRun.objects.filter(task_run=run, feed_id=feed["key"]).exists():
                    FeedRun.objects.create(task_run=run, feed_id=feed["key"], feed_title=feed.get("title") or "",
                                           started_at=now, finished_at=now, status="timeout",
                                           error="the task run timed out",
                                           expect_empty=bool(feed.get("expect_empty")))
        for thread in sub_threads:
            thread.join(max(1.0, deadline - time.monotonic() + 5) if deadline else None)
            if thread.is_alive():
                timed_out = True

        call_error = None
        if call_future is not None and call_future.done() and not call_future.cancelled():
            call_error = call_future.exception()

        # results of this run and of its subtasks, for post
        run_ids = [run.id] + self._descendant_ids(run.id)
        records = list(FeedRun.objects.filter(task_run_id__in=run_ids).order_by("id"))
        results = [
            {"feed_id": r.feed_id, "title": r.feed_title, "status": r.status, "headline_count": r.headline_count,
             "elapsed": (r.finished_at - r.started_at).total_seconds() if r.finished_at else None,
             "http_status": r.http_status, "error": r.error or None, "expect_empty": r.expect_empty}
            for r in records
        ]
        ctx["results"] = results

        status, error = ("timeout", "the task run timed out") if timed_out else ("ok", "")
        if call_error is not None:
            status, error = "failed", f"call: {type(call_error).__name__}: {call_error}"
        if any(getattr(r, "status", None) == "failed" for r in sub_results.values()) and status == "ok":
            status, error = "failed", "a subtask failed"

        # post (TSK2-6b): runs whether the jobs succeeded or not, bounded by post_timeout
        if task.post:
            failure = self._run_post(task, call, ctx)
            if failure:
                with context.scope(source=task.name, task_run=run.id):
                    applog.write("error", f"post of task {task.name} failed: {failure}")
                return finish("failed", f"post: {failure}")

        finish(status, error)
        for name in task.then:  # TSK2-4
            self.enqueue(name, "then")
        return run

    def _run_post(self, task, call, ctx) -> str | None:
        outcome: dict = {}

        def target():
            try:
                for fn_name in task.post:
                    call(fn_name, ctx)
            except Exception as e:  # noqa: BLE001
                outcome["error"] = e
            finally:
                connection.close()

        thread = threading.Thread(target=target, daemon=True, name=f"post-{task.name}")
        thread.start()
        thread.join(task.post_timeout or 300)
        if thread.is_alive():
            return f"timed out after {task.post_timeout:g}s"
        if "error" in outcome:
            e = outcome["error"]
            return f"{type(e).__name__}: {e}"
        return None

    @staticmethod
    def _descendant_ids(run_id) -> list[int]:
        from feedxcavator.models import TaskRun

        found, frontier = [], [run_id]
        while frontier:
            children = list(TaskRun.objects.filter(parent_id__in=frontier).values_list("id", flat=True))
            found.extend(children)
            frontier = children
        return found

    @staticmethod
    def _call_job(call, fn_name, ctx):
        try:
            return call(fn_name, ctx) if _accepts_argument(call, fn_name) else call(fn_name)
        finally:
            connection.close()

    def _feed_job(self, registry, feed, run_id, cancel):
        """TSK-7: one feed, extracted in background mode under the logging source of its extractor."""
        from feedxcavator.models import FeedRun

        record = FeedRun.objects.create(
            task_run_id=run_id, feed_id=feed["key"], feed_title=feed.get("title") or "",
            started_at=timezone.now(), status="running", expect_empty=bool(feed.get("expect_empty")),
        )
        status, error, count, http_status = "ok", "", 0, None
        try:
            with context.scope(source=extraction.extractor_name(feed), task_run=run_id, cancel=cancel):
                try:
                    result = extraction.run_feed(feed, "background", registry)
                    count, http_status = len(result.headlines), result.http_status
                    if result.error:
                        status, error = "error", result.error
                        applog.write("error", f"{feed.get('suffix')}: {result.error}")
                except context.JobCancelled:
                    status, error = "timeout", "the task run timed out"
                except Exception as e:  # noqa: BLE001 - must not affect other jobs
                    status, error = "error", f"{type(e).__name__}: {e}"
                    http_status = context.fetch_state().http_error
                    applog.write("error", e)
        finally:
            try:
                FeedRun.objects.filter(id=record.id, status="running").update(
                    status=status, error=error, headline_count=count, http_status=http_status,
                    finished_at=timezone.now(),
                )
            finally:
                connection.close()
        return count

    def shutdown(self):
        self.parallel.shutdown(wait=False, cancel_futures=True)
        self.sequential.shutdown(wait=False, cancel_futures=True)


def _accepts_argument(call, fn_name) -> bool:
    import inspect

    registry = workspace.get_registry()
    fn = registry.find_task_function(fn_name)
    if fn is None:
        return True
    try:
        return bool(inspect.signature(fn.fn).parameters)
    except (TypeError, ValueError):
        return True


def prune_runs(days: int | None = None) -> int:
    """STO2-6: run records are pruned after `run-history-days`."""
    from feedxcavator.models import TaskRun

    days = days if days is not None else int(conf.get("run-history-days", 30))
    deleted, _ = TaskRun.objects.filter(started_at__lt=timezone.now() - timedelta(days=days)).delete()
    return deleted
