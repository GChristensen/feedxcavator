"""The workspace: loading user code and feed definitions from disk (PORT-REQUIREMENTS sections 4, 5).

A full load builds a new namespace and registry and swaps them in at once (UC2-5). A broken feed file
disables only its feed, a broken lib file keeps the previous state running (D-2).
"""

from __future__ import annotations

import inspect
import io
import linecache
import logging
import sys
import threading
import time
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path

from feedxcavator import conf, context, feeddef, storage, tasks as taskdefs

logger = logging.getLogger("feedxcavator.workspace")

USER_MODULE = "feedxcavator_user"
CODE_SUFFIXES = (".py", ".hy")
IGNORED_DIRS = {".git", ".idea", ".vscode", "__pycache__", "legacy", "node_modules", ".venv"}


class RegistrationError(Exception):
    pass


@dataclass
class FileError:
    path: str
    line: int | None
    message: str
    traceback: str = ""

    def as_dict(self):
        return {"path": self.path, "line": self.line, "message": self.message, "traceback": self.traceback}


@dataclass
class Registration:
    kind: str
    name: str
    fn: object
    file: str
    meta: dict = field(default_factory=dict)


@dataclass
class FeedInfo:
    path: str  # relative to feeds/
    dir: str  # relative to the workspace
    id: str | None = None
    title: str = ""
    suffix: str | None = None
    enabled: bool = True
    tasks: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    status: str = "ok"
    error: str | None = None
    feed: feeddef.Feed | None = None
    mtime: float = 0
    files: list = field(default_factory=list)

    @property
    def key(self) -> str | None:
        """What the stored state of the feed is kept under: its id when it has one, else its suffix."""
        return self.id or self.suffix

    def as_dict(self):
        return {"path": self.path, "dir": self.dir, "id": self.id, "key": self.key, "title": self.title,
                "suffix": self.suffix,
                "enabled": self.enabled, "tasks": self.tasks, "tags": self.tags, "status": self.status,
                "error": self.error}


def norm(name) -> str:
    """Names are compared without the difference between Hy and Python spelling."""
    return str(name).replace("_", "-")


class Registry:
    def __init__(self):
        self.namespace: dict = {}
        self.extractors: dict[str, Registration] = {}
        self.handlers: dict[str, Registration] = {}
        self.failed_handlers: dict[str, str] = {}  # name -> file
        self.proxies: dict[str, dict] = {}
        self.task_functions: dict[str, Registration] = {}
        self.feeds: dict[str, FeedInfo] = {}  # by feed path
        self.tasks: dict[str, taskdefs.TaskDef] = {}
        self.schedules: list[taskdefs.ScheduleDef] = []
        self.errors: list[FileError] = []
        self.generation = 0
        self.loaded_at = 0.0
        self.abandoned = False
        self._by_id: dict[str, FeedInfo] = {}
        self._by_key: dict[str, FeedInfo] = {}
        self._by_suffix: dict[str, FeedInfo] = {}

    # feeds
    def index_feeds(self):
        self._by_id = {info.id: info for info in self.feeds.values() if info.id}
        self._by_key = {info.key: info for info in self.feeds.values() if info.key}
        self._by_suffix = {info.suffix: info for info in self.feeds.values() if info.suffix}

    def info_by_id(self, feed_id) -> FeedInfo | None:
        return self._by_id.get(feed_id)

    def info_by_suffix(self, suffix) -> FeedInfo | None:
        return self._by_suffix.get(suffix)

    def feed_by_id(self, feed_id):
        info = self._by_id.get(feed_id)
        return info.feed if info else None

    def info_by_key(self, key) -> FeedInfo | None:
        return self._by_key.get(key)

    def feed_by_key(self, key):
        info = self._by_key.get(key)
        return info.feed if info else None

    def feed_by_suffix(self, suffix):
        info = self._by_suffix.get(suffix)
        return info.feed if info else None

    def active_feeds(self) -> list:
        return [i.feed for i in self.feeds.values() if i.feed is not None and i.status == "ok" and i.enabled]

    def find_feed(self, key):
        """By suffix, then by title."""
        feed = self.feed_by_suffix(key)
        if feed is not None:
            return feed
        return next((i.feed for i in self.feeds.values() if i.feed is not None and i.title == key), None)

    # code
    def find_extractor(self, name) -> Registration | None:
        return self.extractors.get(norm(name))

    def find_task_function(self, name) -> Registration | None:
        return self.task_functions.get(norm(name))

    # tasks
    def task(self, name: str) -> taskdefs.TaskDef:
        """TSK-1, TSK-2: a declared task, or an implicit one named in the feeds, or an empty one."""
        declared = self.tasks.get(name) or next(
            (t for n, t in self.tasks.items() if n.lower() == name.lower()), None
        )
        return declared or taskdefs.TaskDef(name=name)

    def own_feeds(self, task: taskdefs.TaskDef) -> list:
        return [f for f in self.active_feeds() if taskdefs.feed_matches(task, f)]

    def task_feeds(self, name: str, _seen=None) -> list:
        """The feeds of a task and of its subtasks, without duplicates."""
        _seen = _seen if _seen is not None else set()
        if name in _seen:
            return []
        _seen.add(name)
        task = self.task(name)
        feeds = {f["key"]: f for f in self.own_feeds(task)}
        for sub in task.run:
            for f in self.task_feeds(sub, _seen):
                feeds.setdefault(f["key"], f)
        return list(feeds.values())

    def task_names(self) -> list[str]:
        names = {name: None for name in self.tasks}
        lowered = {n.lower() for n in names}
        for info in self.feeds.values():
            for name in info.tasks:
                if str(name).lower() not in lowered:
                    names[str(name)] = None
                    lowered.add(str(name).lower())
        return sorted(names, key=str.lower)

    def errors_for(self, path: str) -> list[FileError]:
        return [e for e in self.errors if e.path == path]


_live = Registry()
_live_lock = threading.RLock()
_load_lock = threading.RLock()
_listeners: list = []
_unit = None  # the unit being loaded, guarded by _load_lock
_last_signature = None


def get_registry() -> Registry:
    return _live


def add_listener(fn):
    """`fn(registry)` is called after every successful swap."""
    _listeners.append(fn)


# registration (called by user code through the decorators) --------------------------------------


class _Unit:
    """A group of files that load or fail together: a lib file, a feed directory, a handler file."""

    def __init__(self, registry: Registry, label: str):
        self.registry = registry
        self.label = label
        self.file = label
        self.pending: list[Registration] = []
        self.module: types.ModuleType | None = None


def register(kind: str, name: str, fn, **meta):
    unit = _unit
    if unit is None or threading.get_ident() != _load_thread:
        return fn  # outside a load (the scratch file, a REPL): nothing is registered
    name = str(name)
    table = {"extractor": unit.registry.extractors, "handler": unit.registry.handlers,
             "proxy": unit.registry.proxies, "task_function": unit.registry.task_functions}[kind]
    key = name if kind in ("handler", "proxy") else norm(name)
    existing = table.get(key)
    other = existing.file if isinstance(existing, Registration) else (existing or {}).get("_file") if existing else None
    if other is None:
        other = next((p.file for p in unit.pending if p.kind == kind and
                      (p.name if kind in ("handler", "proxy") else norm(p.name)) == key), None)
    if other is not None:
        raise RegistrationError(
            f"{kind.replace('_', ' ')} '{name}' is already registered in {other} "
            f"(second registration in {unit.file})"
        )
    unit.pending.append(Registration(kind, name, fn, unit.file, meta))
    return fn


_load_thread = None


def _commit(unit: _Unit):
    registry = unit.registry
    for reg in unit.pending:
        if reg.kind == "extractor":
            registry.extractors[norm(reg.name)] = reg
        elif reg.kind == "handler":
            registry.handlers[reg.name] = reg
        elif reg.kind == "task_function":
            registry.task_functions[norm(reg.name)] = reg
        elif reg.kind == "proxy":
            registry.proxies[reg.name] = {**reg.fn, "_file": reg.file}


# evaluation ---------------------------------------------------------------------------------


def _new_module(source: dict | None = None) -> types.ModuleType:
    module = types.ModuleType(USER_MODULE)
    if source is not None:
        module.__dict__.update(source)
        for key in ("_hy_macros", "_hy_reader_macros"):
            if isinstance(source.get(key), dict):
                module.__dict__[key] = dict(source[key])
    module.__dict__["__name__"] = USER_MODULE
    return module


def _register_source(relpath: str, source: str):
    """UC2-11: tracebacks at run time point into the workspace."""
    linecache.cache[relpath] = (len(source), None, source.splitlines(True), relpath)


_reader_class = None


def hy_reader():
    """A Hy reader that accepts dots in keywords, for Enlive-style selector vectors (SEL-12)."""
    global _reader_class
    if _reader_class is None:
        from hy.models import Keyword
        from hy.reader.hy_reader import HyReader

        class Reader(HyReader):
            @reader_for(":")  # noqa: F821 - provided by the reader metaclass
            def keyword(self, _):
                return Keyword(self.read_ident(), from_parser=True)

        _reader_class = Reader
    return _reader_class()


def evaluate(source: str, relpath: str, module: types.ModuleType):
    """Evaluates a Python or Hy source text into the namespace of a module."""
    _register_source(relpath, source)
    module.__dict__["__file__"] = relpath
    if relpath.endswith(".hy"):
        from hy.compiler import hy_compile
        from hy.reader import read_many

        previous = sys.modules.get(USER_MODULE)
        sys.modules[USER_MODULE] = module
        try:
            tree = hy_compile(read_many(source, filename=relpath, reader=hy_reader()), module, filename=relpath,
                              source=source)
            code = compile(tree, relpath, "exec")
        finally:
            if previous is not None:
                sys.modules[USER_MODULE] = previous
            else:
                sys.modules.pop(USER_MODULE, None)
    else:
        code = compile(source, relpath, "exec")
    exec(code, module.__dict__)  # noqa: S102 - running user code is the purpose of the workspace


def describe_error(exc: BaseException, relpath: str) -> FileError:
    line = None
    if isinstance(exc, SyntaxError) or getattr(exc, "lineno", None):
        line = getattr(exc, "lineno", None)
    if line is None:
        for frame in reversed(traceback.extract_tb(exc.__traceback__)):
            if frame.filename == relpath:
                line = frame.lineno
                break
    frames = traceback.extract_tb(exc.__traceback__)
    start = next((i for i, f in enumerate(frames) if f.filename == relpath), len(frames))
    text = "".join(traceback.format_list(frames[start:])) + "".join(traceback.format_exception_only(type(exc), exc))
    message = getattr(exc, "msg", None) if isinstance(exc, SyntaxError) else None
    return FileError(relpath, line, f"{type(exc).__name__}: {message or exc}", text)


def _merge(shared: types.ModuleType, unit_module: types.ModuleType, base: types.ModuleType | None = None):
    """UC2-9: the names a unit added or changed go back into the shared namespace. Names the unit
    only inherited from `base` (a group library, WS2-9) do not."""
    target = shared.__dict__
    origin = (base or shared).__dict__
    for key, value in unit_module.__dict__.items():
        if key in ("__file__", "__name__", "__builtins__"):
            continue
        if key in ("_hy_macros", "_hy_reader_macros"):
            if isinstance(value, dict):
                inherited = origin.get(key) if isinstance(origin.get(key), dict) else {}
                target.setdefault(key, {}).update(
                    {k: v for k, v in value.items() if k not in inherited or inherited[k] is not v})
            continue
        if key in origin and origin[key] is value:
            continue
        if key not in target or target[key] is not value:
            target[key] = value


def _code_files(directory: Path, recursive: bool) -> list[Path]:
    if not directory.is_dir():
        return []
    pattern = directory.rglob("*") if recursive else directory.glob("*")
    files = [p for p in pattern if p.is_file() and p.suffix in CODE_SUFFIXES
             and not p.name.startswith((".", "_")) and not (set(p.relative_to(directory).parts[:-1]) & IGNORED_DIRS)]
    return sorted(files, key=lambda p: p.relative_to(directory).as_posix())


def _lib_order(lib: Path, files: list[Path]) -> list[Path]:
    """UC2-3: the order of lib/_order.yaml, then alphabetically."""
    order_file = lib / "_order.yaml"
    if not order_file.exists():
        return files
    import yaml

    try:
        order = yaml.safe_load(order_file.read_text(encoding="utf-8")) or []
        if isinstance(order, dict):
            order = order.get("order") or []
    except yaml.YAMLError as e:
        raise ValueError(f"lib/_order.yaml: {e}") from None
    by_name = {p.relative_to(lib).as_posix(): p for p in files}
    ordered = []
    for name in order:
        name = str(name)
        path = by_name.pop(name, None) or next(
            (by_name.pop(n) for n in list(by_name) if n.rsplit(".", 1)[0] == name), None)
        if path is not None:
            ordered.append(path)
    return ordered + list(by_name.values())


def feed_dirs(root: Path) -> list[Path]:
    """WS2-3: directories under feeds/ that contain feed.yaml. Feeds do not nest, and group
    libraries (WS2-9) are not searched for feeds."""
    feeds_root = root / "feeds"
    found = []

    def walk(directory: Path):
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        if (directory / "feed.yaml").is_file() and directory != feeds_root:
            found.append(directory)
            return
        for entry in entries:
            if entry.is_dir() and entry.name not in IGNORED_DIRS and not entry.name.startswith(".") \
                    and not _is_group_lib(entry):
                walk(entry)

    if feeds_root.is_dir():
        walk(feeds_root)
    return sorted(found, key=lambda p: p.relative_to(feeds_root).as_posix())


def _is_group_lib(directory: Path) -> bool:
    """WS2-9: a `lib` directory in a group folder (feeds/ itself included) that is not a feed."""
    return directory.name == "lib" and directory.is_dir() and not (directory / "feed.yaml").is_file() \
        and not (directory.parent / "feed.yaml").is_file()


def group_dirs(root: Path, feed_dir: Path) -> list[Path]:
    """The group folders of a feed, from feeds/ down to the feed's parent."""
    feeds_root = (root / "feeds").resolve()
    feed_dir = feed_dir.resolve()
    if not feed_dir.is_relative_to(feeds_root) or feed_dir == feeds_root:
        return []
    groups, current = [], feed_dir.parent
    while True:
        groups.append(current)
        if current == feeds_root or current == current.parent:
            break
        current = current.parent
    return groups[::-1]


def template_dirs(root: Path, feed_dir: Path | None = None) -> list[Path]:
    """FD2-6, WS2-9: where `extends` looks, nearest first: the group libraries of the feed, then lib/."""
    groups = group_dirs(root, feed_dir) if feed_dir is not None else []
    return [g / "lib" for g in reversed(groups) if _is_group_lib(g / "lib")] + [root / "lib"]


def template_loader(root: Path, feed_dir: Path | None = None):
    """Loads `extends` templates for a feed in `feed_dir` (or for no feed: lib/ only)."""
    return _make_template_loader(root, template_dirs(root, feed_dir))


def _make_template_loader(root: Path, dirs: list[Path]):
    def locate(name: str, chain=()):
        """The first match not already in `chain`, with a loader that searches on from its directory."""
        escaped, cyclic = True, False
        for index, lib in enumerate(dirs):
            base = (lib / name).resolve()
            if not base.is_relative_to(lib.resolve()):
                continue
            escaped = False
            for candidate in (base.with_name(base.name + ".yaml"), base.with_name(base.name + ".yml"), base):
                if not candidate.is_file():
                    continue
                if str(candidate) in chain:
                    cyclic = True
                    break
                data = feeddef.plain(feeddef.load_yaml(candidate.read_text(encoding="utf-8")))
                if not isinstance(data, dict):
                    raise ValueError(f"template '{name}' must be a mapping")
                return data, _make_template_loader(root, dirs[index:]), str(candidate)
        if cyclic:
            return None
        if escaped:
            raise ValueError(f"template '{name}' is outside lib/")
        where = " or ".join(f"{_rel(root, lib)}/{name}.yaml" for lib in dirs)
        raise ValueError(f"template '{name}' not found (expected {where})")

    def load(name: str) -> dict:
        found = locate(name)
        return found[0]

    load.locate = locate
    return load


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def signature(root: Path) -> tuple:
    """A fingerprint of the workspace files, used to skip reloads that would change nothing."""
    entries = []
    if not root.is_dir():
        return ()
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if set(rel.parts) & IGNORED_DIRS or any(part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and (path.suffix in (*CODE_SUFFIXES, ".yaml", ".yml")) and not path.name.endswith(".tmp"):
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((rel.as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(entries))


# the load -----------------------------------------------------------------------------------


def _load_unit(registry, shared, label, files: list[tuple[str, Path]], isolate=True,
               base: types.ModuleType | None = None, merge=True) -> FileError | None:
    """Evaluates the files of one unit. On success merges its names and commits its registrations.

    An isolated unit evaluates into a copy of `base` (by default the shared namespace). With
    `merge` false its names stay in its own module, `registry._last_unit.module` (WS2-9)."""
    global _unit
    unit = _Unit(registry, label)
    module = _new_module((base or shared).__dict__) if isolate else shared
    unit.module = module
    _unit = unit
    try:
        for relpath, path in files:
            unit.file = relpath
            try:
                evaluate(path.read_text(encoding="utf-8"), relpath, module)
            except BaseException as e:  # noqa: BLE001 - user code may raise anything
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise
                unit.failed_names = [r.name for r in unit.pending if r.kind == "handler"]
                registry._last_failed_unit = unit
                return describe_error(e, relpath)
    finally:
        _unit = None
    if isolate and merge:
        _merge(shared, module, base)
    _commit(unit)
    registry._last_unit = unit
    return None


def build(root: Path, previous: Registry | None, startup: bool) -> Registry:
    """Builds a registry from the workspace. With `abandoned` set, the result must not be swapped in."""
    from feedxcavator import userlib

    registry = Registry()
    shared = _new_module()
    userlib.install(shared)  # a failure here is fatal (UC2-7)
    registry.namespace = shared.__dict__

    # lib/
    lib = root / "lib"
    try:
        lib_files = _lib_order(lib, _code_files(lib, recursive=True))
    except ValueError as e:
        registry.errors.append(FileError("lib/_order.yaml", None, str(e)))
        lib_files = _code_files(lib, recursive=True)
    for path in lib_files:
        relpath = path.relative_to(root).as_posix()
        error = _load_unit(registry, shared, relpath, [(relpath, path)], isolate=False)
        if error:
            registry.errors.append(error)
            if not startup:
                registry.abandoned = True  # UC2-7: the previous namespace stays live
                return registry

    # feeds/
    feeds_root = root / "feeds"
    seen_keys: dict[str, str] = {}  # ids, and lower-cased suffixes: stores are named by them
    seen_suffixes: dict[str, str] = {}
    groups: dict[Path, types.ModuleType | FileError] = {}  # group folder -> its namespace, or its error

    def group_namespace(directory: Path) -> types.ModuleType | FileError:
        """WS2-9: the namespace the feeds of a directory start from, loading group libraries on first use."""
        base: types.ModuleType = shared
        for group in group_dirs(root, directory):
            if group not in groups:
                lib = group / "lib"
                if not _is_group_lib(lib):
                    groups[group] = base
                else:
                    lib_rel = _rel(root, lib)
                    try:
                        code = _lib_order(lib, _code_files(lib, recursive=True))
                    except ValueError as e:
                        registry.errors.append(FileError(f"{lib_rel}/_order.yaml", None, str(e)))
                        code = _code_files(lib, recursive=True)
                    if not code:
                        groups[group] = base
                    else:
                        files = [(_rel(root, p), p) for p in code]
                        error = _load_unit(registry, shared, lib_rel, files, base=base, merge=False)
                        if error:
                            registry.errors.append(error)
                            groups[group] = error
                        else:
                            groups[group] = registry._last_unit.module
            result = groups[group]
            if isinstance(result, FileError):
                return result
            base = result
        return base

    for directory in feed_dirs(root):
        loader = template_loader(root, directory)
        path = directory.relative_to(feeds_root).as_posix()
        rel_dir = directory.relative_to(root).as_posix()
        yaml_rel = f"{rel_dir}/feed.yaml"
        info = FeedInfo(path=path, dir=rel_dir, title=directory.name, suffix=None)
        registry.feeds[path] = info
        code = _code_files(directory, recursive=False)
        info.files = [p.name for p in code]

        def fail(message, line=None, tb="", file=yaml_rel, info=info):
            info.status, info.error = "error", message
            registry.errors.append(FileError(file, line, message, tb))

        def claim(feed_id, suffix, info=info, path=path):
            """Takes the identity for this feed. Returns (message, field) when it is already taken."""
            if suffix and suffix.lower() in seen_suffixes:
                return f"duplicate suffix '{suffix}': already used by feeds/{seen_suffixes[suffix.lower()]}", "suffix"
            key = feed_id or (suffix.lower() if suffix else None)
            if key and key in seen_keys:
                what = f"duplicate id {feed_id}" if feed_id else f"suffix '{suffix}' equals an id"
                return f"{what}: already used by feeds/{seen_keys[key]}", "id" if feed_id else "suffix"
            if suffix:
                seen_suffixes[suffix.lower()] = path
                info.suffix = suffix
            if key:
                seen_keys[key] = path
            info.id = feed_id
            return None

        text = ""
        try:
            yaml_path = directory / "feed.yaml"
            text = yaml_path.read_text(encoding="utf-8")
            info.mtime = yaml_path.stat().st_mtime
            definition, tree = feeddef.parse_definition(text, loader)
            if not definition.get("suffix") and not feeddef.SUFFIX_RE.fullmatch(directory.name):
                raise feeddef.DefinitionError([feeddef.Diagnostic(
                    1, f"the directory name '{directory.name}' cannot be used as the suffix (ASCII letters, "
                       "digits, '.', '-', '_' and '~' only): set `suffix` explicitly")])
        except feeddef.DefinitionError as e:
            # keep the identity when it can be read, so the feed URL still returns the stored output
            identity = feeddef.read_identity(text)
            old = previous.feeds.get(path) if previous else None
            if old is not None:
                info.title, info.tasks = old.title, old.tasks
                identity = {"id": old.id, "suffix": old.suffix, **{k: v for k, v in identity.items() if v}}
            suffix = identity.get("suffix") or directory.name
            claim(identity.get("id"), suffix if feeddef.SUFFIX_RE.fullmatch(suffix) else None)
            info.title = identity.get("title") or info.title
            info.tasks = identity.get("tasks") or info.tasks
            fail("; ".join(str(d) for d in e.diagnostics), e.diagnostics[0].line if e.diagnostics else None)
            continue
        except OSError as e:
            fail(str(e))
            continue

        feed = feeddef.make_feed(definition, path)
        info.title = feed["title"]
        info.enabled, info.tasks, info.tags = feed["enabled"], list(feed["tasks"]), list(feed["tags"])
        taken = claim(feed.get("id"), feed["suffix"])  # FD2-2, FD2-3
        if taken:
            fail(taken[0], feeddef.line_of(tree, (taken[1],)))
            continue

        base = group_namespace(directory)
        if isinstance(base, FileError):  # WS2-9: a broken group library disables the feeds of its group
            fail(f"the group library failed to load: {base.path}: {base.message}")
            continue

        if code:  # WS2-4, UC2-8, UC2-9
            files = [(p.relative_to(root).as_posix(), p) for p in code]
            error = _load_unit(registry, shared, rel_dir, files, base=base)
            if error:
                fail(error.message, error.line, error.traceback, file=error.path)
                continue
            own = [r for r in registry._last_unit.pending if r.kind == "extractor"]
            if not feed.get("extractor") and len(own) == 1:  # UC2-14
                feed = feed.replace(extractor=own[0].name)
        info.feed = feed

    # handlers/
    for path in _code_files(root / "handlers", recursive=True):
        relpath = path.relative_to(root).as_posix()
        error = _load_unit(registry, shared, relpath, [(relpath, path)])
        if error:  # UC2-10
            registry.errors.append(error)
            names = set(registry._last_failed_unit.failed_names)
            if previous is not None:
                names.update(n for n, r in previous.handlers.items() if r.file == relpath)
                names.update(n for n, f in previous.failed_handlers.items() if f == relpath)
            for name in names:
                registry.failed_handlers[name] = relpath

    # feeds that name an extractor or a transform that does not exist
    for info in registry.feeds.values():
        feed = info.feed
        if feed is None:
            continue
        if feed.get("extractor") and registry.find_extractor(feed["extractor"]) is None:
            info.status, info.error = "error", f"extractor '{feed['extractor']}' is not defined"
            registry.errors.append(FileError(f"{info.dir}/feed.yaml", None, info.error))

    # tasks.yaml and schedules.yaml, read after the code (UC2-3)
    for name, parser, target in (("tasks.yaml", taskdefs.parse_tasks, "tasks"),
                                 ("schedules.yaml", taskdefs.parse_schedules, "schedules")):
        path = root / name
        if not path.is_file():
            continue
        try:
            setattr(registry, target, parser(path.read_text(encoding="utf-8")))
        except feeddef.DefinitionError as e:
            for d in e.diagnostics:
                registry.errors.append(FileError(name, d.line, d.message))
        except OSError as e:
            registry.errors.append(FileError(name, None, str(e)))
    for name in taskdefs.find_cycles(registry.tasks):  # TSK2-3
        registry.tasks[name].error = "the task is part of a cycle of 'run'"
        registry.errors.append(FileError("tasks.yaml", None, f"task '{name}' is part of a cycle of 'run'"))
    for task in registry.tasks.values():
        for fn in (*task.pre, *task.post, *([task.call] if task.call else [])):
            if registry.find_task_function(fn) is None:
                task.error = task.error or f"task function '{fn}' is not defined"
                registry.errors.append(FileError("tasks.yaml", None, f"task '{task.name}': {task.error}"))

    registry.index_feeds()
    for attr in ("_last_unit", "_last_failed_unit"):
        registry.__dict__.pop(attr, None)
    return registry


def load(startup: bool = False, force: bool = True) -> Registry:
    """A full load (UC2-3 to UC2-10). Returns the registry that was built: the live one, or an
    abandoned one whose `errors` explain why the previous state was kept."""
    global _live, _load_thread, _last_signature
    from feedxcavator import fetch

    root = conf.workspace_dir()
    with _load_lock:
        current = signature(root)
        if not force and current == _last_signature:
            return _live
        _load_thread = threading.get_ident()
        started = time.time()
        try:
            previous = _live if _live.generation else None
            registry = build(root, previous, startup or previous is None)
        finally:
            _load_thread = None
        _last_signature = current
        registry.loaded_at = time.time()
        if registry.abandoned:
            logger.error("workspace load abandoned: %s", "; ".join(f"{e.path}: {e.message}" for e in registry.errors))
            _live.errors = [e for e in _live.errors if not e.path.startswith("lib/")] + registry.errors
            _report(registry)
            return registry
        with _live_lock:
            registry.generation = _live.generation + 1
            _live = registry
        fetch.proxy_resolver = registry.proxies.get
        logger.info("workspace loaded in %.2fs: %d feeds, %d errors", time.time() - started,
                    len(registry.feeds), len(registry.errors))
        _report(registry)
        try:
            sync_index(registry)
        except Exception as e:  # noqa: BLE001 - the index is only a cache
            logger.warning("failed to update the feed index: %s", e)
        for listener in list(_listeners):
            try:
                listener(registry)
            except Exception:  # noqa: BLE001
                logger.exception("a workspace listener failed")
        return registry


def _report(registry: Registry):
    """UC2-7: load errors are logged."""
    from feedxcavator import applog

    if not registry.errors:
        return
    global _reported
    digest = tuple((e.path, e.line, e.message) for e in registry.errors)
    if digest == _reported:
        return
    _reported = digest
    with context.scope(source="workspace"):
        for error in registry.errors:
            where = f"{error.path}:{error.line}" if error.line else error.path
            applog.write("error", f"{where}: {error.message}\n{error.traceback}".rstrip())


_reported = None


def sync_index(registry: Registry):
    """STO2-2: FeedIndex mirrors the feeds of the workspace."""
    from django.db import transaction

    from feedxcavator.models import FeedIndex

    wanted = {}
    for info in registry.feeds.values():
        if not info.key:
            continue
        definition = info.feed.as_dict() if info.feed is not None else {}
        wanted[info.key] = {
            "suffix": info.suffix, "path": info.path, "title": info.title or "", "tasks": info.tasks,
            "tags": info.tags, "enabled": info.enabled, "status": info.status, "error": info.error or "",
            "definition": _jsonable(definition), "mtime": info.mtime,
        }
    with transaction.atomic():
        existing = {row.id: row for row in FeedIndex.objects.all()}
        stale = [i for i in existing if i not in wanted]
        if stale:
            FeedIndex.objects.filter(id__in=stale).delete()
        # suffixes may move between feeds: clear the ones that change first
        for feed_id, fields in wanted.items():
            row = existing.get(feed_id)
            if row is not None and row.suffix != fields["suffix"]:
                FeedIndex.objects.filter(id=feed_id).update(suffix=None)
        for feed_id, fields in wanted.items():
            row = existing.get(feed_id)
            if row is None or any(getattr(row, k) != v for k, v in fields.items()):
                FeedIndex.objects.update_or_create(id=feed_id, defaults=fields)


def _jsonable(value):
    import json

    return json.loads(json.dumps(value, default=str))


# watching -----------------------------------------------------------------------------------


def _watch_filter(change, path: str) -> bool:
    p = Path(path)
    if set(p.parts) & IGNORED_DIRS or p.name.endswith(".tmp") or p.name.startswith("."):
        return False
    return True


def start_watcher(debounce_ms: int = 500) -> threading.Thread:
    """UC2-6: reloads when workspace files change, however they were changed (WS2-5)."""
    root = conf.workspace_dir()
    root.mkdir(parents=True, exist_ok=True)

    def run():
        from watchfiles import watch

        while True:
            try:
                for _ in watch(root, debounce=debounce_ms, step=100, watch_filter=_watch_filter,
                               raise_interrupt=False):
                    try:
                        load(force=False)
                    except Exception:  # noqa: BLE001
                        logger.exception("workspace reload failed")
                    finally:
                        _close_connection()
            except Exception:  # noqa: BLE001
                logger.exception("the workspace watcher failed, restarting it")
                time.sleep(5)

    thread = threading.Thread(target=run, daemon=True, name="workspace-watcher")
    thread.start()
    return thread


def _close_connection():
    from django.db import connection

    connection.close()


def ensure_layout():
    """Creates an empty workspace skeleton on first start."""
    root = conf.workspace_dir()
    for name in ("lib", "feeds", "handlers"):
        (root / name).mkdir(parents=True, exist_ok=True)
    defaults = {
        "tasks.yaml": "# Tasks: see the API tab of the console.\ntasks: {}\n",
        "schedules.yaml": "# Schedules: managed on the Schedules tab of the console.\nschedules: []\n",
        "scratch.hy": '; Run from the console. Sees the live namespace, never changes it.\n(print "Hello from Hy")\n',
    }
    for name, content in defaults.items():
        path = root / name
        if name.startswith("scratch") and (root / "scratch.py").exists():
            continue
        if not path.exists():
            path.write_text(content, encoding="utf-8")


# scratch ------------------------------------------------------------------------------------


def run_scratch(source: str, relpath: str = "scratch.hy") -> dict:
    """UC2-20: runs code in an isolated copy of the live namespace and returns what it printed."""
    from feedxcavator import applog

    applog.install_stdout()
    module = _new_module(get_registry().namespace)
    buffer = io.StringIO()
    token = context.stdout_capture.set(buffer)
    error = None
    try:
        context.new_fetch_state()
        with context.scope(source="scratch", mode="test"):
            evaluate(source, relpath, module)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, (KeyboardInterrupt, SystemExit)) and not isinstance(e, SystemExit):
            raise
        error = describe_error(e, relpath)
    finally:
        context.stdout_capture.reset(token)
    output = buffer.getvalue()
    if error:
        output = (output + ("\n" if output and not output.endswith("\n") else "") + error.traceback).rstrip() + "\n"
    return {"output": output, "error": error.as_dict() if error else None}


def outline_kinds(fn) -> str:
    return "function" if inspect.isfunction(fn) else "value"
