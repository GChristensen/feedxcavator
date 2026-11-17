import threading
import time
from datetime import timedelta

import pytest
from django.utils import timezone

from conftest import html
from feedxcavator import conf, maintenance, storage, worker
from feedxcavator.models import Blob, FeedRun, LogEntry, QueueItem, ScheduleState, TaskRun
from feedxcavator.runner import TaskRunner

PAGE = '<article><h2>T{n}</h2><a href="/p/{n}">l</a></article>'


def feed_yaml(http, name, extra="", title=None):
    return (f"title: {title or name}\nsource: {http.url}/{name}\n"
            "selectors: {item: article, title: h2, link: a}\n" + extra)


@pytest.fixture
def runner():
    runner = TaskRunner(parallel=4, sequential=1)
    yield runner
    runner.wait_idle(10)
    runner.shutdown()


LIB = """
import threading, time
EVENTS = []
GATE = threading.Event()

@task_function("pre-ok")
def pre_ok(ctx):
    EVENTS.append(("pre", ctx.task, ctx.trigger, sorted(f.suffix for f in ctx.feeds)))

@task_function("pre-skip")
def pre_skip(ctx):
    return False

@task_function("pre-fail")
def pre_fail(ctx):
    raise RuntimeError("login failed")

@task_function("post-ok")
def post_ok(ctx):
    EVENTS.append(("post", ctx.task, sorted((r["title"], r["status"], r["headline_count"]) for r in ctx.results)))

@task_function("post-fail")
def post_fail(ctx):
    raise ValueError("no summary")

@task_function("post-slow")
def post_slow(ctx):
    time.sleep(5)

@task_function("job")
def job():
    EVENTS.append(("call",))

@task_function("job-fail")
def job_fail():
    raise KeyError("nope")

@extractor("slow")
def slow(feed):
    GATE.wait(10)
    fetch_url(feed.source)
    return [{"title": "late", "link": "x"}]

@extractor("boom")
def boom(feed):
    raise RuntimeError("extractor exploded")
"""


def test_task_run_order_and_records(http, ws, runner):
    for name in ("a", "b", "c"):
        http.routes[f"/{name}"] = html(PAGE.format(n=name))
    http.routes["/empty"] = html("<p>none</p>")
    registry = ws({
        "lib/fns.py": LIB,
        "feeds/a/feed.yaml": feed_yaml(http, "a", "tasks: [news]\n"),
        "feeds/b/feed.yaml": feed_yaml(http, "b", "tasks: [news]\nparallel: false\n"),
        "feeds/c/feed.yaml": feed_yaml(http, "c", "tasks: [articles]\n"),
        "feeds/empty/feed.yaml": feed_yaml(http, "empty", "tasks: [articles]\n"),
        "feeds/quiet/feed.yaml": feed_yaml(http, "empty", "tasks: [articles]\nexpect_empty: true\n", title="quiet"),
        "feeds/bad/feed.yaml": feed_yaml(http, "bad", "tasks: [articles]\nextractor: boom\n"),
        "tasks.yaml": "tasks:\n  morning:\n    pre: pre-ok\n    run: [news, articles]\n    post: post-ok\n"
                      "    then: [after]\n  after: {call: job}\n",
    }, startup=True)
    assert registry.errors == []
    run = runner.run("morning", "manual")
    assert runner.wait_idle(10)
    assert run.status == "ok"
    events = registry.namespace["EVENTS"]
    assert events[0] == ("pre", "morning", "manual", ["a", "b", "bad", "c", "empty", "quiet"])  # TSK2-6a
    assert events[1][0] == "post" and events[2] == ("call",)  # TSK2-6c: pre, jobs, post, then
    assert events[1][2] == [("a", "ok", 1), ("b", "ok", 1), ("bad", "error", 0), ("c", "ok", 1),
                            ("empty", "ok", 0), ("quiet", "ok", 0)]  # TSK2-6b

    runs = {r.task: r for r in TaskRun.objects.all()}
    assert set(runs) == {"morning", "news", "articles", "after"}
    assert runs["news"].parent_id == run.id and runs["news"].trigger == "subtask"
    assert runs["after"].trigger == "then" and runs["after"].status == "ok"
    bad = FeedRun.objects.get(feed_title="bad")
    assert bad.status == "error" and "extractor exploded" in bad.error  # TSK-7: other jobs are unaffected
    assert FeedRun.objects.get(feed_title="quiet").expect_empty
    assert storage.fetch_feed_output(registry.feeds["a"].key)["item_count"] == 1
    assert storage.fetch_feed_output(registry.feeds["empty"].key) is None

    assert LogEntry.objects.filter(source="boom", level="error").count() == 1  # the logging source
    zero = list(LogEntry.objects.filter(level="warning", message__contains="zero headlines")
                .values_list("message", flat=True))
    assert zero == ["empty: zero headlines"]  # TSK2-8: not for expect_empty or failed feeds


def test_pre_and_post_outcomes(http, ws, runner):
    http.routes["/a"] = html(PAGE.format(n=1))
    registry = ws({
        "lib/fns.py": LIB,
        "feeds/a/feed.yaml": feed_yaml(http, "a", "tasks: [t]\n"),
        "tasks.yaml": "tasks:\n  skipped: {pre: pre-skip, run: [t], post: post-ok, then: [after]}\n"
                      "  failing: {pre: [pre-ok, pre-fail], run: [t], post: post-ok, then: [after]}\n"
                      "  badpost: {run: [t], post: post-fail, then: [after]}\n"
                      "  slowpost: {run: [t], post: post-slow, post_timeout: 0.3, then: [after]}\n"
                      "  after: {call: job}\n",
    }, startup=True)
    events = registry.namespace["EVENTS"]

    assert runner.run("skipped").status == "skipped"
    failing = runner.run("failing")
    assert failing.status == "failed" and "login failed" in failing.error
    assert FeedRun.objects.count() == 0  # no feed jobs started
    assert [e for e in events if e[0] in ("post", "call")] == []  # post does not run, then is not enqueued

    badpost = runner.run("badpost")
    assert badpost.status == "failed" and "no summary" in badpost.error
    assert FeedRun.objects.filter(status="ok").count() == 1  # the jobs did run
    slowpost = runner.run("slowpost")
    assert slowpost.status == "failed" and "timed out" in slowpost.error
    runner.wait_idle(5)
    assert ("call",) not in events


def test_call_failure_is_logged(ws, runner):
    ws({"lib/fns.py": LIB, "tasks.yaml": "tasks:\n  broken: {call: job-fail}\n"}, startup=True)
    run = runner.run("broken")
    assert run.status == "failed" and "nope" in run.error
    entry = LogEntry.objects.get(source="broken", level="error")
    assert entry.message.startswith("call of task broken failed: KeyError")


def test_timeout_cancels_jobs_and_post_still_runs(http, ws, runner):
    http.routes["/a"] = html(PAGE.format(n=1))
    registry = ws({
        "lib/fns.py": LIB,
        "feeds/slow/feed.yaml": feed_yaml(http, "a", "tasks: [t]\nextractor: slow\n", title="slow"),
        "feeds/fast/feed.yaml": feed_yaml(http, "a", "tasks: [t]\n", title="fast"),
        "tasks.yaml": "tasks:\n  t: {timeout: 0.5, post: post-ok}\n",
    }, startup=True)
    started = time.monotonic()
    run = runner.run("t")
    assert time.monotonic() - started < 4
    assert run.status == "timeout"
    statuses = dict(FeedRun.objects.values_list("feed_title", "status"))
    assert statuses == {"slow": "timeout", "fast": "ok"}  # TSK2-7
    post = next(e for e in registry.namespace["EVENTS"] if e[0] == "post")
    assert ("slow", "timeout", None) in post[2] and ("fast", "ok", 1) in post[2]  # TSK2-6b
    warning = LogEntry.objects.get(source="t", level="warning")
    assert warning.message == "task t timed out; feeds not finished: slow"
    registry.namespace["GATE"].set()  # the late job must neither store output nor change its record
    time.sleep(0.5)
    assert storage.fetch_feed_output(registry.feeds["slow"].key) is None
    assert FeedRun.objects.get(feed_title="slow").status == "timeout"


def test_concurrency_policies(http, ws, runner):
    http.routes["/a"] = html(PAGE.format(n=1))
    registry = ws({
        "lib/fns.py": LIB,
        "feeds/s/feed.yaml": feed_yaml(http, "a", "tasks: [skipper, queued, parallel]\nextractor: slow\n"),
        "tasks.yaml": "tasks:\n  skipper: {}\n  queued: {concurrency: queue}\n  parallel: {concurrency: allow}\n",
    }, startup=True)
    gate = registry.namespace["GATE"]

    def overlap(name):
        gate.clear()
        TaskRun.objects.all().delete()
        first = runner.enqueue(name)
        deadline = time.monotonic() + 5
        while not FeedRun.objects.filter(status="running").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        second = runner.enqueue(name)
        time.sleep(0.3)
        during = sorted(TaskRun.objects.values_list("status", flat=True))
        gate.set()
        first.join(10)
        second.join(10)
        return during, sorted(TaskRun.objects.values_list("status", flat=True))

    assert overlap("skipper") == (["running", "skipped"], ["ok", "skipped"])  # TSK2-5
    assert overlap("queued") == (["running"], ["ok", "ok"])
    assert overlap("parallel") == (["running", "running"], ["ok", "ok"])


def test_cycle_and_unknown_tasks(ws, runner):
    ws({"tasks.yaml": "tasks:\n  a: {run: [b]}\n  b: {run: [a]}\n"}, startup=True)
    assert runner.run("a").status == "failed"  # TSK2-3
    assert runner.run("nothing-here").status == "ok"  # TSK-2


def test_scheduler_and_queue(ws):
    ws({"schedules.yaml": "schedules:\n  - task: news\n    cron: '*/5 * * * *'\n"
                          "  - task: missed\n    cron: '0 * * * *'\n    misfire: run-once\n"
                          "  - task: off\n    cron: '* * * * *'\n    enabled: false\n"}, startup=True)
    fired = []
    scheduler = worker.Scheduler(fired.append)
    scheduler.reload()
    assert fired == [] and ScheduleState.objects.count() == 2  # first seen: nothing to make up
    now = timezone.now()
    scheduler.tick(now + timedelta(minutes=5, seconds=1))
    assert fired == ["news"]
    assert ScheduleState.objects.get(key__startswith="news|").last_fired_at is not None  # SCH2-5
    scheduler.tick(now + timedelta(minutes=5, seconds=2))
    assert fired == ["news"]  # fires once per slot
    scheduler.tick(now + timedelta(minutes=65))
    assert sorted(fired) == ["missed", "news", "news"]

    # a new worker start after downtime: run-once fires once, skip does not (SCH2-3)
    ScheduleState.objects.update(last_fired_at=now - timedelta(hours=5))
    fired.clear()
    restarted = worker.Scheduler(fired.append)
    restarted.reload()
    assert fired == ["missed"]
    restarted.reload()
    assert fired == ["missed"]  # SCH-6: a reload does not fire again

    worker.enqueue_task("news", "manual")
    worker.enqueue("service-task")
    assert QueueItem.objects.count() == 2
    w = worker.Worker()
    handled = []
    w.handle = lambda item: handled.append((item.kind, item.payload))
    w.poll()
    assert handled == [("task", {"name": "news", "trigger": "manual"}), ("service-task", {})]
    assert QueueItem.objects.count() == 0
    w.runner.shutdown()


def test_service_task(ws):
    ws({}, startup=True)
    old = storage.store_blob(b"old image", "image/png")
    storage.store_blob(b"new image", "image/png")
    Blob.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(hours=49))
    stale_ms = int((time.time() - 49 * 3600) * 1000)
    LogEntry.objects.create(level="info", timestamp=stale_ms, message="stale")
    TaskRun.objects.create(task="t", trigger="manual", status="ok", started_at=timezone.now() - timedelta(days=40))
    storage.store_object("gone", 1, ttl=0.001)
    storage.store_object("kept", {"a": [1]})
    time.sleep(0.01)
    counts = maintenance.service_task()
    assert counts["images"] == 1 and counts["log_entries"] == 1 and counts["runs"] == 1 and counts["objects"] == 1
    assert Blob.objects.count() == 1 and not storage.blob_path(old.sha256).exists()
    assert storage.fetch_object("kept") == {"a": [1]} and storage.fetch_object("gone", "default") == "default"
    entry = LogEntry.objects.get(source="service-task")
    assert entry.level == "info" and "purged 1 of 1 images" in entry.message  # MNT-1


def test_service_task_max_age_setting(ws, monkeypatch):
    ws({}, startup=True)
    monkeypatch.setitem(conf.config, "service-task", {"max-age-days": 5})
    blob = storage.store_blob(b"three days old", "image/png")
    Blob.objects.filter(id=blob.id).update(created_at=timezone.now() - timedelta(days=3))
    LogEntry.objects.create(level="info", timestamp=int((time.time() - 3 * 86400) * 1000), message="kept")
    LogEntry.objects.create(level="info", timestamp=int((time.time() - 6 * 86400) * 1000), message="purged")
    counts = maintenance.service_task()
    assert counts["images"] == 0 and counts["log_entries"] == 1
    assert "older than 5 days" in LogEntry.objects.get(source="service-task").message


@pytest.mark.parametrize("value, expected", [("04:00", (4, 0)), ("23:59", (23, 59)), ("7:05", (7, 5)), (240, (4, 0))])
def test_service_task_time(monkeypatch, value, expected):
    monkeypatch.setitem(conf.config, "service-task", {"time": value})
    assert conf.service_task_time() == expected  # an int is an unquoted YAML 1.1 sexagesimal time


@pytest.mark.parametrize("value", ["24:00", "4pm", "12:60"])
def test_service_task_time_invalid(monkeypatch, value):
    monkeypatch.setitem(conf.config, "service-task", {"time": value})
    with pytest.raises(ValueError):
        conf.service_task_time()


def test_service_task_schedule_uses_time(monkeypatch):
    monkeypatch.setitem(conf.config, "service-task", {"time": "13:45"})
    at = worker.Scheduler._next_service()
    assert (at.hour, at.minute) == (13, 45) and at > __import__("datetime").datetime.now().astimezone()


def test_service_task_enabled(monkeypatch):
    monkeypatch.setitem(conf.config, "service-task", None)
    monkeypatch.setitem(conf.config, "execute-daily-service-task", None)
    assert conf.service_task("enabled") is False and conf.service_task("max-age-days") == 2
    monkeypatch.setitem(conf.config, "execute-daily-service-task", True)
    assert conf.service_task("enabled") is True  # the 3.0.0 key
    monkeypatch.setitem(conf.config, "service-task", {"enabled": False})
    assert conf.service_task("enabled") is False


def test_blobs_share_identical_content():
    first = storage.store_blob(b"same bytes", "image/jpeg", "tg://c/1")
    second = storage.store_blob(b"same bytes", "image/jpeg")
    assert first.sha256 == second.sha256 and first.id != second.id  # STO2-11
    assert storage.find_blob_by_source("tg://c/1").id == first.id
    storage.delete_blob(first)
    assert storage.blob_path(second.sha256).exists()
    storage.delete_blob(second)
    assert not storage.blob_path(second.sha256).exists()
    assert storage.sanitize_path("a b/c:d?.jpg") == "a_b/c_d_.jpg"  # STO-6
