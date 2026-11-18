"""Task and schedule definitions from tasks.yaml and schedules.yaml."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from feedxcavator.feeddef import DefinitionError, Diagnostic, line_of, load_yaml, plain

_DURATION = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*", re.I)
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value) -> float | None:
    """`30m`, `2h`, `45s`, `1h30m`, or a number of seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    total, pos, text = 0.0, 0, str(value)
    while pos < len(text):
        m = _DURATION.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"invalid duration '{value}': expected for example 30m, 2h or 45s")
        total += float(m.group(1)) * _UNITS[(m.group(2) or "s").lower()]
        pos = m.end()
    return total


def _names(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tags: list[str] = []
    path: str | list[str] | None = Field(None, description="Glob over the feed path, e.g. news/local/*")
    suffixes: list[str] = Field([], alias="list", description="Feed suffixes")

    @field_validator("tags", "suffixes", mode="before")
    @classmethod
    def _listify(cls, value):
        return _names(value)


class TaskModel(_Strict):
    feeds: FeedSelector | None = None
    run: list[str] = Field([], description="Subtasks, started together")
    then: list[str] = Field([], description="Tasks enqueued when this one has finished")
    pre: list[str] = Field([], description="Task functions run before the feed jobs")
    post: list[str] = Field([], description="Task functions run after the feed jobs")
    call: str | None = Field(None, description="A task function run as one job")
    concurrency: Literal["skip", "queue", "allow"] = "skip"
    timeout: float | None = None
    post_timeout: float = 300

    @field_validator("run", "then", "pre", "post", mode="before")
    @classmethod
    def _listify(cls, value):
        return _names(value)

    @field_validator("timeout", "post_timeout", mode="before")
    @classmethod
    def _duration(cls, value):
        return parse_duration(value)


class TasksFile(_Strict):
    tasks: dict[str, TaskModel | None] = {}


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


class ScheduleModel(_Strict):
    task: str
    cron: str = Field(description="A five-field cron expression")
    timezone: str | None = None
    misfire: Literal["skip", "run-once"] = "skip"
    enabled: bool = True
    jitter: float = Field(0, ge=0, description="Random delay of each firing, up to this many seconds")

    @field_validator("cron")
    @classmethod
    def _cron(cls, value):
        value = " ".join(str(value).split())
        if len(value.split(" ")) != 5 or not croniter.is_valid(value):
            raise ValueError("expected a five-field cron expression, for example '0 12 * * *'")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value):
        if value and not valid_timezone(value):
            raise ValueError(f"unknown time zone '{value}'")
        return value


class SchedulesFile(_Strict):
    schedules: list[ScheduleModel] = []


@dataclass
class TaskDef:
    name: str
    declared: bool = False
    tags: list = field(default_factory=list)
    paths: list = field(default_factory=list)
    suffixes: list = field(default_factory=list)
    run: list = field(default_factory=list)
    then: list = field(default_factory=list)
    pre: list = field(default_factory=list)
    post: list = field(default_factory=list)
    call: str | None = None
    concurrency: str = "skip"
    timeout: float | None = None
    post_timeout: float = 300
    error: str | None = None


@dataclass
class ScheduleDef:
    task: str
    cron: str
    timezone: str | None = None
    misfire: str = "skip"
    enabled: bool = True
    jitter: float = 0
    index: int = 0

    @property
    def key(self) -> str:
        return f"{self.task}|{self.cron}|{self.timezone or ''}"

    def tzinfo(self):
        if self.timezone:
            return ZoneInfo(self.timezone)
        return local_timezone()

    def next_fire(self, after: datetime | None = None) -> datetime:
        tz = self.tzinfo()
        base = (after or datetime.now(tz)).astimezone(tz)
        return croniter(self.cron, base).get_next(datetime)

    def next_fires(self, count: int, after: datetime | None = None) -> list[datetime]:
        tz = self.tzinfo()
        iterator = croniter(self.cron, (after or datetime.now(tz)).astimezone(tz))
        return [iterator.get_next(datetime) for _ in range(count)]


def local_timezone():
    """The server's local zone."""
    try:
        from tzlocal import get_localzone

        return get_localzone()
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone().tzinfo


def _diagnostics(error: ValidationError, tree) -> list[Diagnostic]:
    result = []
    for err in error.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"])
        message = "unknown key" if err["type"] == "extra_forbidden" else err["msg"].removeprefix("Value error, ")
        result.append(Diagnostic(line_of(tree, err["loc"]), f"{loc}: {message}" if loc else message))
    return result


def parse_tasks(text: str) -> dict[str, TaskDef]:
    """Parses tasks.yaml. Raises DefinitionError."""
    tree = load_yaml(text)
    raw = plain(tree) if tree is not None else {}
    try:
        model = TasksFile.model_validate(raw)
    except ValidationError as e:
        raise DefinitionError(_diagnostics(e, tree)) from None
    tasks = {}
    for name, task in model.tasks.items():
        task = task or TaskModel()
        selector = task.feeds or FeedSelector()
        tasks[str(name)] = TaskDef(
            name=str(name), declared=True, tags=selector.tags, paths=_names(selector.path), suffixes=selector.suffixes,
            run=task.run, then=task.then, pre=task.pre, post=task.post, call=task.call,
            concurrency=task.concurrency, timeout=task.timeout, post_timeout=task.post_timeout,
        )
    return tasks


def parse_schedules(text: str) -> list[ScheduleDef]:
    """Parses schedules.yaml. Duplicates are ignored. Raises DefinitionError."""
    tree = load_yaml(text)
    raw = plain(tree) if tree is not None else {}
    try:
        model = SchedulesFile.model_validate(raw)
    except ValidationError as e:
        raise DefinitionError(_diagnostics(e, tree)) from None
    schedules, seen = [], set()
    for index, item in enumerate(model.schedules):
        schedule = ScheduleDef(item.task, item.cron, item.timezone, item.misfire, item.enabled, item.jitter, index)
        if schedule.key in seen:
            continue
        seen.add(schedule.key)
        schedules.append(schedule)
    return schedules


def find_cycles(tasks: dict[str, TaskDef]) -> set[str]:
    """The names of the tasks that take part in a cycle of `run`."""
    in_cycle: set[str] = set()

    def visit(name, stack):
        if name in stack:
            in_cycle.update(stack[stack.index(name):])
            return
        task = tasks.get(name)
        if task is None:
            return
        for sub in task.run:
            visit(sub, [*stack, name])

    for name in tasks:
        visit(name, [])
    return in_cycle


def tasks_schema() -> dict:
    return TasksFile.model_json_schema(by_alias=True)


def schedules_schema() -> dict:
    return SchedulesFile.model_json_schema()


def feed_matches(task: TaskDef, feed) -> bool:
    """How a task selects feeds: tags, path globs, suffix list, and the feed's own `tasks` field."""
    name = task.name.lower()
    if any(str(t).lower() == name for t in feed.get("tasks") or []):
        return True
    if task.tags and set(task.tags) & set(feed.get("tags") or []):
        return True
    if task.suffixes and feed.get("suffix") in task.suffixes:
        return True
    path = feed.get("path") or ""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in task.paths)
