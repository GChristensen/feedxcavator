"""The console API: JSON over HTTP under /front/api/ (API2-1)."""

from __future__ import annotations

import functools
import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

from asgiref.sync import sync_to_async
from django.http import HttpResponse
from django.utils import timezone
from ninja import File, NinjaAPI, Query, Schema
from ninja.files import UploadedFile

from feedxcavator import (VERSION, applog, auto, backup, conf, context, extraction, feeddef, filters, lifecycle,
                          output, storage, tasks as taskdefs, userlib, workspace)

api = NinjaAPI(title="Feedxcavator console API", version=VERSION, docs_url=None, openapi_url=None)

EDITABLE_SUFFIXES = {".yaml", ".yml", ".py", ".hy", ".clj", ".md", ".txt", ".json", ".xml", ".html", ".css",
                     ".js", ""}


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        self.status, self.message, self.extra = status, message, extra
        super().__init__(message)


@api.exception_handler(ApiError)
def _api_error(request, exc: ApiError):
    return api.create_response(request, {"error": exc.message, **exc.extra}, status=exc.status)


def blocking(fn):
    """Runs an endpoint in a thread of its own, so long operations do not block other requests (PRC-5)."""

    def call(*args, **kwargs):
        from django.db import connections

        try:
            return fn(*args, **kwargs)
        finally:
            connections.close_all()

    runner = sync_to_async(call, thread_sensitive=False)

    @functools.wraps(fn)
    async def endpoint(*args, **kwargs):
        return await runner(*args, **kwargs)

    return endpoint


# paths --------------------------------------------------------------------------------------------


def root() -> Path:
    return conf.workspace_dir().resolve()


def resolve(path: str, must_exist: bool = False) -> Path:
    path = (path or "").replace("\\", "/").strip("/")
    target = (root() / path).resolve()
    if not target.is_relative_to(root()) or any(part in workspace.IGNORED_DIRS - {"legacy"} or part.startswith(".")
                                                for part in Path(path).parts):
        raise ApiError(400, f"invalid path: {path}")
    if must_exist and not target.exists():
        raise ApiError(404, f"not found: {path}")
    return target


def rel(path: Path) -> str:
    return path.resolve().relative_to(root()).as_posix()


def language(name: str) -> str:
    return {".yaml": "yaml", ".yml": "yaml", ".py": "python", ".hy": "hy", ".clj": "clojure", ".json": "json",
            ".xml": "xml", ".md": "markdown", ".js": "javascript", ".html": "html",
            ".css": "css"}.get(Path(name).suffix, "text")


def load_report(registry=None) -> dict:
    live = workspace.get_registry()
    registry = registry or live
    return {
        "ok": not registry.errors and not registry.abandoned,
        "abandoned": registry.abandoned,
        "generation": live.generation,
        "loaded_at": live.loaded_at,
        "errors": [e.as_dict() for e in (registry.errors if registry.abandoned else live.errors)],
    }


def reload() -> dict:
    return load_report(workspace.load(force=True))


# tree ---------------------------------------------------------------------------------------------


def _tree(directory: Path, feeds: dict) -> list[dict]:
    nodes = []
    try:
        # a group library (WS2-9) is listed first in its folder; the top-level lib/ keeps its place
        nested = directory != root()
        entries = sorted(directory.iterdir(),
                         key=lambda p: (not (nested and p.name == "lib" and p.is_dir()), p.is_file(), p.name.lower()))
    except OSError:
        return nodes
    for entry in entries:
        if entry.name.startswith(".") or entry.name in workspace.IGNORED_DIRS - {"legacy"} or \
                entry.name.endswith(".tmp"):
            continue
        path = rel(entry)
        if entry.is_dir():
            info = feeds.get(path)
            node = {"name": entry.name, "path": path, "type": "feed" if info else "dir",
                    "children": _tree(entry, feeds)}
            if info:
                node["feed"] = info.as_dict()
                node["children"] = [c for c in node["children"] if c["name"] != "feed.yaml"]
            nodes.append(node)
        else:
            nodes.append({"name": entry.name, "path": path, "type": "file", "lang": language(entry.name)})
    return nodes


@api.get("/tree")
@blocking
def tree(request):
    registry = workspace.get_registry()
    feeds = {info.dir: info for info in registry.feeds.values()}
    error_paths = sorted({e.path for e in registry.errors})
    return {"tree": _tree(root(), feeds), "errors": error_paths, "load": load_report()}


@api.get("/feeds")
@blocking
def feeds(request):
    return [info.as_dict() for info in sorted(workspace.get_registry().feeds.values(), key=lambda i: i.title.lower())]


# files --------------------------------------------------------------------------------------------


class FileIn(Schema):
    path: str
    content: str = ""
    base_mtime: float | None = None
    force: bool = False


class PathIn(Schema):
    path: str


class CreateIn(Schema):
    path: str
    kind: str = "file"  # file | dir
    content: str = ""


class MoveIn(Schema):
    src: str
    dst: str


def diagnostics_for(path: str, content: str) -> list[dict]:
    """UI2-5: validation of the YAML files against their schemas."""
    name = Path(path).name
    try:
        if name == "feed.yaml" and path.startswith("feeds/"):
            loader = workspace.template_loader(root(), (root() / path).parent)
            return [d.as_dict() for d in feeddef.diagnose(content, loader)]
        if path == "tasks.yaml":
            taskdefs.parse_tasks(content)
        elif path == "schedules.yaml":
            taskdefs.parse_schedules(content)
        elif name.endswith((".yaml", ".yml")):
            feeddef.load_yaml(content)
    except feeddef.DefinitionError as e:
        return [d.as_dict() for d in e.diagnostics]
    return []


@api.get("/file")
@blocking
def read_file(request, path: str):
    target = resolve(path, must_exist=True)
    if not target.is_file():
        raise ApiError(400, f"not a file: {path}")
    if target.suffix not in EDITABLE_SUFFIXES:
        raise ApiError(415, f"{target.name} is not a text file the console can edit")
    registry = workspace.get_registry()
    return {"path": rel(target), "content": target.read_text(encoding="utf-8"), "mtime": target.stat().st_mtime,
            "lang": language(target.name), "errors": [e.as_dict() for e in registry.errors_for(rel(target))]}


@api.put("/file")
@blocking
def write_file(request, data: FileIn):
    """WS2-5, WS2-6, UI2-10, UI2-11: an atomic write followed by a reload whose result is returned."""
    target = resolve(data.path)
    path = rel(target)
    if target.is_dir():
        raise ApiError(400, f"{path} is a directory")
    if target.exists() and data.base_mtime is not None and not data.force:
        if abs(target.stat().st_mtime - data.base_mtime) > 0.0005:
            raise ApiError(409, "the file was changed on disk", mtime=target.stat().st_mtime)
    problems = diagnostics_for(path, data.content)
    if problems:  # FD2-4: nothing is stored
        raise ApiError(400, "validation failed", diagnostics=problems)
    storage.write_atomic(target, data.content)
    report = reload()
    live = workspace.get_registry()
    related = [e for e in report["errors"] if e["path"] == path or e["path"].startswith(path.rsplit("/", 1)[0] + "/")]
    return {"path": path, "mtime": target.stat().st_mtime, "load": report, "file_errors": related,
            "feed": next((i.as_dict() for i in live.feeds.values() if path.startswith(i.dir + "/")), None)}


@api.post("/file/validate")
@blocking
def validate_file(request, data: FileIn):
    return {"diagnostics": diagnostics_for(data.path, data.content)}


@api.post("/file/create")
@blocking
def create_file(request, data: CreateIn):
    target = resolve(data.path)
    if target.exists():
        raise ApiError(409, f"{rel(target)} already exists")
    if data.kind == "dir":
        target.mkdir(parents=True)
    else:
        storage.write_atomic(target, data.content)
    return {"path": rel(target), "load": reload()}


@api.post("/file/move")
@blocking
def move_file(request, data: MoveIn):
    """Renames or moves a file or a directory. A feed keeps its identity (WS2-7): the id is in feed.yaml."""
    source, target = resolve(data.src, must_exist=True), resolve(data.dst)
    if target.exists():
        raise ApiError(409, f"{rel(target)} already exists")
    if source == root() or target.is_relative_to(source):
        raise ApiError(400, "cannot move a directory into itself")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)
    return {"path": rel(target), "load": reload()}


@api.delete("/file")
@blocking
def delete_file(request, path: str):
    from feedxcavator import websub

    target = resolve(path, must_exist=True)
    if target == root():
        raise ApiError(400, "cannot delete the workspace")
    registry = workspace.get_registry()
    removed = [i for i in registry.feeds.values()
               if (root() / i.dir).resolve() == target or (root() / i.dir).resolve().is_relative_to(target)
               or (target.name == "feed.yaml" and (root() / i.dir).resolve() == target.parent)]
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    for info in removed:  # WS-9
        if info.key:
            websub.delete_feed_subscriptions(info.key)
            storage.delete_feed_output(info.key)
    return {"load": reload()}


# feeds --------------------------------------------------------------------------------------------


class NewFeedIn(Schema):
    dir: str = ""  # a directory under feeds/ ("" for the top level)


class FeedTextIn(Schema):
    path: str
    yaml: str


class FeedKeyIn(Schema):
    key: str


class RenameSuffixIn(Schema):
    path: str  # the feed directory or its feed.yaml
    suffix: str


@api.post("/feed/new")
@blocking
def new_feed(request, data: NewFeedIn):
    """FD2-7: creates a feed directory from the template."""
    base = resolve("feeds/" + data.dir.strip("/")) if data.dir.strip("/") else resolve("feeds")
    registry = workspace.get_registry()
    for info in registry.feeds.values():  # feeds do not nest
        feed_dir = (root() / info.dir).resolve()
        if base == feed_dir or base.is_relative_to(feed_dir):
            base = feed_dir.parent
            break
    suffixes = {i.suffix.lower() for i in registry.feeds.values() if i.suffix}
    name, n = "untitled-feed", 1
    while (base / name).exists() or name in suffixes:
        n += 1
        name = f"untitled-feed-{n}"
    target = base / name / "feed.yaml"
    storage.write_atomic(target, feeddef.NEW_FEED_TEMPLATE.format(suffix=name))
    return {"path": rel(target), "dir": rel(target.parent), "load": reload()}


@api.post("/feed/rename-suffix")
@blocking
def rename_suffix(request, data: RenameSuffixIn):
    """Changes the suffix of a feed in its feed.yaml and moves the stored state (output, history,
    subscriptions, run records) to the new name. A feed with an `id` keeps its state under the id,
    so only the definition changes."""
    target = resolve(data.path, must_exist=True)
    yaml_path = target if target.name == "feed.yaml" else target / "feed.yaml"
    if not yaml_path.is_file() or not rel(yaml_path).startswith("feeds/"):
        raise ApiError(400, f"{data.path} is not a feed")
    suffix = data.suffix.strip()
    if not feeddef.SUFFIX_RE.fullmatch(suffix):
        raise ApiError(400, "a suffix may contain only ASCII letters, digits, '.', '-', '_' and '~', "
                            "and must start with a letter or a digit")
    registry = workspace.get_registry()
    feed_dir = rel(yaml_path.parent)
    info = next((i for i in registry.feeds.values() if i.dir == feed_dir), None)
    if info is None or not info.suffix:
        raise ApiError(409, "the feed is not loaded: fix its errors first")
    if suffix == info.suffix:
        return {"suffix": suffix, "moved": None, "load": load_report(), "mtime": yaml_path.stat().st_mtime}
    clash = next((i for i in registry.feeds.values()
                  if i is not info and (i.suffix or "").lower() == suffix.lower()), None)
    if clash is not None:
        raise ApiError(409, f"suffix '{suffix}' is already used by {clash.dir}")
    if any(i is not info and i.id == suffix for i in registry.feeds.values()):
        raise ApiError(409, f"'{suffix}' is the id of another feed")
    text = yaml_path.read_text(encoding="utf-8")
    updated = feeddef.set_suffix(text, suffix)
    problems = diagnostics_for(rel(yaml_path), updated)
    if problems:
        raise ApiError(400, "the definition does not validate, fix it before renaming", diagnostics=problems)
    old_key = info.key
    storage.write_atomic(yaml_path, updated)
    moved = None
    if not info.id:  # the state is kept under the suffix
        moved = storage.rename_feed_state(old_key, suffix)
    return {"suffix": suffix, "moved": moved, "load": reload(), "mtime": yaml_path.stat().st_mtime,
            "content": updated}


def _feed_from_text(path: str, text: str):
    """Builds a feed from unsaved text, with the code that is currently loaded."""
    target = resolve(path)
    feed_dir = target.parent
    try:
        definition, _ = feeddef.parse_definition(text, workspace.template_loader(root(), feed_dir))
    except feeddef.DefinitionError as e:
        raise ApiError(400, "validation failed", diagnostics=[d.as_dict() for d in e.diagnostics]) from None
    feed_path = feed_dir.relative_to(root() / "feeds").as_posix()
    feed = feeddef.make_feed(definition, feed_path)
    registry = workspace.get_registry()
    info = registry.feeds.get(feed_path)
    if not feed.get("extractor") and info is not None:
        own = [r for r in registry.extractors.values() if r.file.startswith(info.dir + "/")]
        if len(own) == 1:  # UC2-14
            feed = feed.replace(extractor=own[0].name)
    return feed


@api.post("/feed/test")
@blocking
def test_feed(request, data: FeedTextIn):
    """API-6: runs the unsaved definition in test mode. Nothing is stored or published."""
    feed = _feed_from_text(data.path, data.yaml)
    with context.scope(source=extraction.extractor_name(feed)):
        result = extraction.run_feed(feed, "test")
    text = result.output["output"] if result.output else ""
    content_type = result.output["content_type"] if result.output else "text/plain"
    if "xml" in content_type:
        text = output.xml_format(text)
    elif "json" in content_type:
        import json

        text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    return {"output": text, "content_type": content_type, "count": len(result.headlines), "error": result.error}


@api.post("/feed/suggest")
@blocking
def suggest_selectors(request, data: FeedTextIn):
    """AUTO-2: proposes a `selectors` block for the current source. Nothing is saved."""
    feed = _feed_from_text(data.path, data.yaml)
    if not feed.source_url:
        raise ApiError(400, "the feed has no source")
    selectors = auto.suggest_for_url(feed.source_url, feed.get("fetch"))
    if not selectors:
        from feedxcavator import fetch

        status = fetch.get_last_http_error()
        reason = f"HTTP error {status}" if status else "no repeated item structure was found on the page"
        raise ApiError(422, f"could not suggest selectors: {reason}")
    return {"selectors": selectors, "yaml": auto.selectors_yaml(selectors)}


@api.get("/feed/url")
@blocking
def feed_url(request, key: str):
    feed = workspace.get_registry().feed_by_key(key)
    if feed is None:
        raise ApiError(404, "feed not found")
    return {"url": output.get_feed_url(feed)}


@api.post("/feed/run-tasks")
@blocking
def run_feed_tasks(request, data: FeedKeyIn):
    """API-8: enqueues the tasks the feed belongs to."""
    from feedxcavator import worker

    feed = workspace.get_registry().feed_by_key(data.key)
    if feed is None:
        raise ApiError(404, "feed not found")
    names = list(feed.get("tasks") or [])
    for name in names:
        worker.enqueue_task(name, "manual")
    return {"enqueued": names}


# load status, scratch, reference -------------------------------------------------------------------


@api.get("/load-status")
@blocking
def load_status(request):
    return load_report()


@api.post("/reload")
@blocking
def reload_workspace(request):
    return reload()


class ScratchIn(Schema):
    path: str = "scratch.hy"
    content: str


@api.post("/scratch/run")
@blocking
def run_scratch(request, data: ScratchIn):
    """UC2-20."""
    return workspace.run_scratch(data.content, data.path)


@api.get("/reference")
@blocking
def reference(request):
    return {"sections": userlib.api_reference(), "version": VERSION}


@api.get("/schemas")
@blocking
def schemas(request):
    return {"feed": feeddef.json_schema(), "tasks": taskdefs.tasks_schema(),
            "schedules": taskdefs.schedules_schema()}


# tasks --------------------------------------------------------------------------------------------


class TaskIn(Schema):
    task: str


@api.get("/tasks")
@blocking
def list_tasks(request):
    registry = workspace.get_registry()
    result = []
    for name in registry.task_names():
        task = registry.task(name)
        result.append({"name": name, "declared": task.declared, "feeds": len(registry.task_feeds(name)),
                       "run": task.run, "then": task.then, "pre": task.pre, "post": task.post, "call": task.call,
                       "concurrency": task.concurrency, "timeout": task.timeout, "error": task.error})
    return result


@api.get("/tasks/feeds")
@blocking
def task_feeds(request, task: str):
    """TSK-10."""
    return [{"key": f["key"], "title": f["title"], "suffix": f["suffix"]}
            for f in workspace.get_registry().task_feeds(task)]


@api.post("/tasks/run")
@blocking
def run_task(request, data: TaskIn):
    from feedxcavator import worker

    worker.enqueue_task(data.task, "manual")
    return {"enqueued": data.task}


# The Tasks tab edits tasks.yaml field by field. Lists are sent as lists of strings; an empty value removes
# the key. tags, path and list go under `feeds`.
TASK_LIST_FIELDS = ("run", "then", "pre", "post")
TASK_SCALAR_FIELDS = ("call", "concurrency", "timeout", "post_timeout")
TASK_FEED_FIELDS = ("tags", "path", "list")
TASK_NAME_RE = re.compile(r"^[^\s:#{}\[\],&*!|>'\"%@`]+$")


class TaskSaveIn(Schema):
    name: str
    fields: dict = {}


def _raw_tasks() -> tuple[dict, bool]:
    """The `tasks` mapping of tasks.yaml as plain values, and whether the file could be read."""
    path = root() / "tasks.yaml"
    if not path.exists():
        return {}, True
    try:
        tree = feeddef.load_yaml(path.read_text(encoding="utf-8"))
    except feeddef.DefinitionError:
        return {}, False
    raw = feeddef.plain(tree) if tree is not None else {}
    tasks = raw.get("tasks") if isinstance(raw, dict) else None
    return (tasks if isinstance(tasks, dict) else {}), True


def _as_list(value) -> list[str]:
    if value is None or value == "":
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _as_text(value) -> str:
    return "" if value is None else str(value)


@api.get("/tasks/table")
@blocking
def tasks_table(request):
    """The Tasks tab: tasks.yaml as written, plus the tasks named only by feeds (TSK2-2)."""
    from feedxcavator.models import TaskRun

    registry = workspace.get_registry()
    raw, readable = _raw_tasks()
    scheduled: dict[str, int] = {}
    for schedule in registry.schedules:
        scheduled[schedule.task.lower()] = scheduled.get(schedule.task.lower(), 0) + 1
    names = list(raw) + [n for n in registry.task_names() if n not in raw]
    rows = []
    for name in names:
        entry = raw.get(name) if isinstance(raw.get(name), dict) else {}
        selector = entry.get("feeds") if isinstance(entry.get("feeds"), dict) else {}
        task = registry.task(name)
        last = TaskRun.objects.filter(task__iexact=name, parent__isnull=True).order_by("-id").first()
        rows.append({
            "name": name, "declared": name in raw,
            "tags": _as_list(selector.get("tags")), "path": _as_list(selector.get("path")),
            "list": _as_list(selector.get("list")),
            **{f: _as_list(entry.get(f)) for f in TASK_LIST_FIELDS},
            "call": _as_text(entry.get("call")), "concurrency": _as_text(entry.get("concurrency")) or "skip",
            "timeout": _as_text(entry.get("timeout")), "post_timeout": _as_text(entry.get("post_timeout")),
            "feeds": len(registry.task_feeds(name)), "schedules": scheduled.get(name.lower(), 0),
            "error": task.error,
            "last_status": last.status if last else None,
            "last_started_at": last.started_at.isoformat() if last and last.started_at else None,
        })
    errors = [e.as_dict() for e in registry.errors_for("tasks.yaml")]
    if not readable and not errors:
        errors = [{"path": "tasks.yaml", "line": None, "message": "tasks.yaml cannot be parsed"}]
    path = root() / "tasks.yaml"
    return {"tasks": rows, "errors": errors, "functions": sorted(r.name for r in registry.task_functions.values()),
            "mtime": path.stat().st_mtime if path.exists() else None}


def _flow_list(values):
    from ruamel.yaml.comments import CommentedSeq

    seq = CommentedSeq(values)
    seq.fa.set_flow_style()
    return seq


def _edit_tasks(mutate) -> dict:
    """Edits tasks.yaml preserving its comments, as _edit_schedules does for schedules.yaml."""
    from ruamel.yaml.comments import CommentedMap

    path = root() / "tasks.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else "tasks: {}\n"
    try:
        tree = feeddef.load_yaml(text)
    except feeddef.DefinitionError as e:
        raise ApiError(400, "tasks.yaml has a syntax error; fix it in the Workspace tab",
                       diagnostics=[d.as_dict() for d in e.diagnostics]) from None
    if tree is None:
        tree = CommentedMap()
    if not isinstance(tree, dict):
        raise ApiError(400, "tasks.yaml is not a mapping; fix it in the Workspace tab")
    if not isinstance(tree.get("tasks"), dict):
        tree["tasks"] = CommentedMap()
    result = mutate(tree["tasks"])
    updated = feeddef.dump_yaml(tree)
    try:
        taskdefs.parse_tasks(updated)
    except feeddef.DefinitionError as e:
        raise ApiError(400, "validation failed", diagnostics=[d.as_dict() for d in e.diagnostics]) from None
    storage.write_atomic(path, updated)
    return {"load": reload(), **(result or {})}


def _task_key(tasks, name: str):
    """The key of a task in tasks.yaml: as spelled, or matching without case (TSK-1)."""
    if name in tasks:
        return name
    return next((k for k in tasks if str(k).lower() == name.lower()), None)


def _set(mapping, key, value):
    if value in (None, "", []):
        mapping.pop(key, None)
    else:
        mapping[key] = value


@api.post("/tasks/save")
@blocking
def save_task(request, data: TaskSaveIn):
    """Adds a task to tasks.yaml or changes fields of one. A task named only by feeds becomes declared."""
    from ruamel.yaml.comments import CommentedMap

    name = data.name.strip()
    if not TASK_NAME_RE.fullmatch(name):
        raise ApiError(400, f"'{name}' is not a usable task name")
    unknown = set(data.fields) - {*TASK_LIST_FIELDS, *TASK_SCALAR_FIELDS, *TASK_FEED_FIELDS}
    if unknown:
        raise ApiError(400, f"unknown task fields: {', '.join(sorted(unknown))}")

    def value_of(field, value):
        if field in TASK_LIST_FIELDS or field in ("tags", "list", "path"):
            values = [str(v).strip() for v in (value or []) if str(v).strip()]
            if field == "path" and len(values) == 1:
                return values[0]
            return _flow_list(values) if values else None
        value = "" if value is None else str(value).strip()
        if field in ("timeout", "post_timeout") and re.fullmatch(r"\d+(\.\d+)?", value):
            return float(value) if "." in value else int(value)
        return value

    def mutate(tasks):
        key = _task_key(tasks, name)
        if key is None:
            key = name
            tasks[key] = CommentedMap()
        entry = tasks[key]
        if not isinstance(entry, dict):  # `name:` with no value
            entry = tasks[key] = CommentedMap()
        for field, value in data.fields.items():
            value = value_of(field, value)
            if field in TASK_FEED_FIELDS:
                selector = entry.get("feeds")
                if not isinstance(selector, dict):
                    selector = CommentedMap()
                    selector.fa.set_flow_style()
                _set(selector, field, value)
                _set(entry, "feeds", selector if selector else None)
            elif field == "concurrency" and value == "skip" and "concurrency" not in entry:
                continue
            else:
                _set(entry, field, value)
        if not entry:
            tasks[key] = None
        return {"name": str(key)}

    return _edit_tasks(mutate)


@api.post("/tasks/delete")
@blocking
def delete_task(request, data: TaskIn):
    def mutate(tasks):
        key = _task_key(tasks, data.task)
        if key is None:
            raise ApiError(404, f"tasks.yaml does not declare {data.task}")
        del tasks[key]

    return _edit_tasks(mutate)


# schedules ----------------------------------------------------------------------------------------


class ScheduleIn(Schema):
    index: int | None = None  # None adds a schedule
    task: str | None = None
    cron: str | None = None
    timezone: str | None = None
    misfire: str | None = None
    enabled: bool | None = None
    jitter: float | None = None


def _describe(cron: str) -> str:
    try:
        from cron_descriptor import Options, get_description

        options = Options()
        options.use_24hour_time_format = True
        return get_description(cron, options)
    except Exception:  # noqa: BLE001
        return ""


@api.get("/schedules")
@blocking
def list_schedules(request):
    """UI2-8."""
    from feedxcavator.models import ScheduleState, TaskRun

    registry = workspace.get_registry()
    states = {s.key: s for s in ScheduleState.objects.all()}
    rows = []
    for schedule in registry.schedules:
        state = states.get(schedule.key)
        last_run = TaskRun.objects.filter(task__iexact=schedule.task, parent__isnull=True).order_by("-id").first()
        rows.append({
            "index": schedule.index, "task": schedule.task, "cron": schedule.cron,
            "description": _describe(schedule.cron),
            "timezone": schedule.timezone, "effective_timezone": str(schedule.tzinfo()),
            "misfire": schedule.misfire, "enabled": schedule.enabled, "jitter": schedule.jitter,
            "next": [d.isoformat() for d in schedule.next_fires(1)] if schedule.enabled else [],
            "last_fired_at": state.last_fired_at.isoformat() if state and state.last_fired_at else None,
            "last_status": last_run.status if last_run else None,
        })
    path = root() / "schedules.yaml"
    return {"schedules": rows, "errors": [e.as_dict() for e in registry.errors_for("schedules.yaml")],
            "tasks": registry.task_names(), "mtime": path.stat().st_mtime if path.exists() else None}


def _edit_schedules(mutate) -> dict:
    """Edits schedules.yaml preserving its comments (ruamel round trip)."""
    path = root() / "schedules.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else "schedules: []\n"
    tree = feeddef.load_yaml(text)
    if tree is None:
        tree = feeddef.load_yaml("schedules: []\n")
    if tree.get("schedules") is None:
        tree["schedules"] = feeddef.load_yaml("[]\n")
    mutate(tree["schedules"])
    updated = feeddef.dump_yaml(tree)
    try:
        taskdefs.parse_schedules(updated)
    except feeddef.DefinitionError as e:
        raise ApiError(400, "validation failed", diagnostics=[d.as_dict() for d in e.diagnostics]) from None
    storage.write_atomic(path, updated)
    return {"load": reload()}


@api.post("/schedules/save")
@blocking
def save_schedule(request, data: ScheduleIn):
    fields = {k: v for k, v in data.dict().items() if k != "index" and v is not None}

    def mutate(schedules):
        if data.index is None:
            if not fields.get("task") or not fields.get("cron"):
                raise ApiError(400, "a schedule needs a task and a cron expression")
            entry = {"task": fields["task"], "cron": fields["cron"]}
            entry.update({k: v for k, v in fields.items() if k not in entry})
            schedules.append(entry)
            return
        if not 0 <= data.index < len(schedules):
            raise ApiError(404, "no such schedule")
        entry = schedules[data.index]
        for key, value in fields.items():
            if key == "timezone" and value == "":
                entry.pop("timezone", None)
            elif key == "enabled" and value is True and "enabled" not in entry:
                continue
            else:
                entry[key] = value

    return _edit_schedules(mutate)


@api.post("/schedules/delete")
@blocking
def delete_schedule(request, data: ScheduleIn):
    def mutate(schedules):
        if data.index is None or not 0 <= data.index < len(schedules):
            raise ApiError(404, "no such schedule")
        del schedules[data.index]

    return _edit_schedules(mutate)


# runs ---------------------------------------------------------------------------------------------


def _duration(started, finished):
    return (finished - started).total_seconds() if started and finished else None


def _run_dict(run, children_of) -> dict:
    return {
        "id": run.id, "task": run.task, "trigger": run.trigger, "status": run.status, "error": run.error,
        "started_at": run.started_at.isoformat(), "duration": _duration(run.started_at, run.finished_at),
        "feed_runs": [
            {"feed_id": f.feed_id, "title": f.feed_title, "status": f.status, "headline_count": f.headline_count,
             "http_status": f.http_status, "error": f.error, "duration": _duration(f.started_at, f.finished_at),
             "zero": f.status == "ok" and not f.headline_count and not f.expect_empty}
            for f in run.feed_runs.all().order_by("id")
        ],
        "children": [_run_dict(child, children_of) for child in children_of.get(run.id, [])],
    }


@api.get("/runs")
@blocking
def runs(request, limit: int = 50, before: int | None = None):
    """UI2-9."""
    from feedxcavator.models import TaskRun

    query = TaskRun.objects.filter(parent__isnull=True).order_by("-id")
    if before:
        query = query.filter(id__lt=before)
    top = list(query.prefetch_related("feed_runs")[: max(1, min(limit, 200))])
    children_of: dict = {}
    frontier = [r.id for r in top]
    while frontier:
        children = list(TaskRun.objects.filter(parent_id__in=frontier).prefetch_related("feed_runs").order_by("id"))
        for child in children:
            children_of.setdefault(child.parent_id, []).append(child)
        frontier = [c.id for c in children]
    return [_run_dict(run, children_of) for run in top]


@api.delete("/runs")
@blocking
def clear_runs(request):
    """Deletes the finished task runs with their subtask and feed runs. Runs still in progress are kept, because
    the runner keeps writing to their records."""
    from feedxcavator.models import TaskRun

    finished = TaskRun.objects.filter(parent__isnull=True).exclude(status="running")
    count = finished.count()
    finished.delete()
    return {"deleted": count}


# log ----------------------------------------------------------------------------------------------


@api.get("/log")
@blocking
def log_entries(request, n: int = 20, from_: int | None = Query(None, alias="from")):
    return applog.read_entries(min(n, 500), from_)


@api.delete("/log")
@blocking
def clear_log(request):
    applog.clear()
    return {"ok": True}


@api.get("/server-log")
@blocking
def server_log(request):
    """API-15."""
    path = conf.LOG_DIR / "app.log"
    if not path.exists():
        return {"content": ""}
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 1024 * 1024))
        return {"content": f.read().decode("utf-8", "replace")}


# word filters (STO2-9, WF-1, WF-2) ---------------------------------------------------------------


class WordFilterIn(Schema):
    name: str
    text: str = ""


class WordFilterRenameIn(Schema):
    name: str
    new_name: str


def _filter_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ApiError(400, "a word filter needs a name")
    if len(name) > 255:
        raise ApiError(400, "the name is longer than 255 characters")
    return name


def _filter_users() -> dict[str, list[dict]]:
    """Feeds by the word filter their `wordfilter` steps name."""
    users: dict[str, list[dict]] = {}
    for info in workspace.get_registry().feeds.values():
        for step in (info.feed.get("filter") or []) if info.feed is not None else []:
            if isinstance(step, dict) and step.get("wordfilter"):
                name = step["wordfilter"].get("name") or "default"
                users.setdefault(name, []).append({"title": info.title, "dir": info.dir})
    return users


@api.get("/wordfilters")
@blocking
def list_wordfilters(request):
    from django.db.models import Count

    from feedxcavator.models import WordFilter

    users = _filter_users()
    rows = {f.name: {"name": f.name, "count": f.count, "feeds": users.get(f.name, []), "exists": True}
            for f in WordFilter.objects.annotate(count=Count("entries"))}
    for name, feeds_ in users.items():  # named by a feed but not created yet
        rows.setdefault(name, {"name": name, "count": 0, "feeds": feeds_, "exists": False})
    return sorted(rows.values(), key=lambda r: r["name"].lower())


@api.get("/wordfilter")
@blocking
def read_wordfilter(request, name: str):
    text = filters.filter_text(name)
    return {"name": name, "text": text or "", "exists": text is not None}


@api.put("/wordfilter")
@blocking
def save_wordfilter(request, data: WordFilterIn):
    name = _filter_name(data.name)
    entries, errors = filters.parse_filter_text(data.text)
    if errors:
        raise ApiError(400, "the word filter has errors", diagnostics=errors)
    filters.replace_filter_entries(name, entries)
    return {"name": name, "count": len(entries), "text": filters.filter_text(name)}


@api.post("/wordfilter/create")
@blocking
def create_wordfilter(request, data: WordFilterIn):
    from feedxcavator.models import WordFilter

    name = _filter_name(data.name)
    if WordFilter.objects.filter(name=name).exists():
        raise ApiError(409, f"the word filter {name} already exists")
    WordFilter.objects.create(name=name)
    return {"name": name}


@api.post("/wordfilter/rename")
@blocking
def rename_wordfilter(request, data: WordFilterRenameIn):
    from feedxcavator.models import WordFilter

    new_name = _filter_name(data.new_name)
    word_filter = WordFilter.objects.filter(name=data.name).first()
    if word_filter is None:
        raise ApiError(404, f"no word filter {data.name}")
    if new_name != data.name and WordFilter.objects.filter(name=new_name).exists():
        raise ApiError(409, f"the word filter {new_name} already exists")
    word_filter.name = new_name
    word_filter.save()
    return {"name": new_name, "feeds": _filter_users().get(data.name, [])}


@api.delete("/wordfilter")
@blocking
def delete_wordfilter(request, name: str):
    from feedxcavator.models import WordFilter

    deleted, _ = WordFilter.objects.filter(name=name).delete()
    if not deleted:
        raise ApiError(404, f"no word filter {name}")
    return {"ok": True}


# settings, token, database, server -----------------------------------------------------------------


class SettingsIn(Schema):
    subscription_url: str | None = None


@api.get("/settings")
@blocking
def get_settings(request):
    from feedxcavator import rpc
    from feedxcavator.models import ApiToken, Blob, Settings

    settings = Settings.load()
    token = ApiToken.objects.order_by("-id").first()
    return {
        "subscription_url": settings.subscription_url,
        "version": VERSION, "restartable": conf.supervised(),
        "token_created_at": token.created_at.isoformat() if token else None,
        "images": Blob.objects.count(), "worker_alive": rpc.worker_alive(),
        "authentication": conf.get("authentication", True) is not False,
        "workspace": str(conf.workspace_dir()), "workspace_empty": backup.workspace_is_empty(),
    }


@api.put("/settings")
@blocking
def save_settings(request, data: SettingsIn):
    from feedxcavator.models import Settings

    settings = Settings.load()
    if data.subscription_url is not None:
        settings.subscription_url = data.subscription_url.strip()
    settings.save()
    return {"ok": True}


@api.post("/token")
@blocking
def generate_token(request):
    """STO2-14: the token is shown once. Only its hash is stored."""
    from feedxcavator.models import ApiToken
    from feedxcavator.views import token_hash

    token = secrets.token_hex(20)
    ApiToken.objects.all().delete()
    ApiToken.objects.create(token_hash=token_hash(token), created_at=timezone.now())
    return {"token": token}


@api.get("/backup")
@blocking
def download_backup(request, workspace: bool = False):
    content = backup.create_backup(include_workspace=workspace)
    response = HttpResponse(content, content_type="application/zip")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    response["Content-Disposition"] = f"attachment; filename=feedxcavator-backup-{stamp}.zip"
    return response


@api.post("/restore")
@blocking
def restore(request, file: UploadedFile = File(...), unpack_workspace: bool = False):
    import zipfile

    try:
        result = backup.restore_backup(file.read(), unpack_workspace)
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise ApiError(400, f"not a valid backup: {e}") from None
    if result["workspace_unpacked"]:
        result["load"] = reload()
    return result


@api.post("/purge-images")
@blocking
def purge_images(request):
    return {"deleted": storage.delete_all_blobs()}


@api.post("/restart")
@blocking
def restart(request):
    """LFC-2."""
    if not conf.supervised():
        raise ApiError(409, "Restart is available only when Feedxcavator is started by feedxcavator.cmd, "
                            "feedxcavator.sh or the systemd service")
    applog.write("info", "server restart requested from the web UI")
    lifecycle.request_restart()
    return {"ok": True}
