"""Feed definitions: parsing, validation and the Feed object (PORT-REQUIREMENTS section 7)."""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError, YAMLError

from feedxcavator import selectors as sel

ID_RE = re.compile(r"[0-9a-f]{32}")
SUFFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]*")
FILTER_STEPS = ("history", "dedupe", "wordfilter", "exclude", "limit")

NEW_FEED_TEMPLATE = """\
title: '*Untitled feed'
suffix: {suffix}
source: https://example.com
#id: 0123456789abcdef0123456789abcdef   # optional: keeps the stored state when the suffix changes
#enabled: true
#tags: [news]
#extends: forums/phpbb
#mode: selectors            # or auto
#fetch:
#  backend: http            # or browser
#  impersonate: chrome
#  method: GET
#  headers: {{Accept-Language: en}}
#  cookies: {{name: value}}
#  charset: auto
#  proxy: default
#  timeout: 30
#  retry: 2
#  wait_for: article        # browser backend
#  scroll: 0                # browser backend
#selectors:
#  item: div.headline
#  title: h3
#  link: a
#  summary: div.preview
#  image: div.cover img:first-of-type
#  author: div.user-name
#  category: {{css: 'div.category a::text', many: true}}
#  published: {{css: 'time::attr(datetime)', parse: date}}
#pages:
#  include-source: true
#  path: '/?page={{n}}'
#  increment: 1
#  start: 2
#  end: 2
#  delay: 0
#pages:
#  next: 'a.next::attr(href)'
#  max: 5
#filter:
#  - history: {{key: link, keep: 1000}}
#  - dedupe: link
#  - wordfilter: {{name: default, fields: [title, summary]}}
#  - exclude: {{field: title, regex: '^Sponsored'}}
#  - limit: 50
#fulltext: false
#transform: function-name
#output: rss                # rss, atom, json-feed, json
#tasks: [task-name]
#parallel: true
#websub: {{enabled: true, partition: 100}}
#expect_empty: false
#extractor: extractor-name
#params: [any additional data, [123, 456]]
"""


@dataclass
class Diagnostic:
    line: int
    message: str

    def as_dict(self):
        return {"line": self.line, "message": self.message}

    def __str__(self):
        return f"line {self.line}: {self.message}"


class DefinitionError(Exception):
    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = diagnostics
        super().__init__("; ".join(str(d) for d in diagnostics))


# YAML -------------------------------------------------------------------------


def yaml_rt() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_yaml(text: str):
    """Parses YAML keeping line information. Raises DefinitionError on a syntax error."""
    try:
        return yaml_rt().load(text)
    except MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line = (mark.line + 1) if mark else 1
        message = " ".join(str(part) for part in (e.context, e.problem) if part)
        raise DefinitionError([Diagnostic(line, f"YAML syntax error: {message}")]) from None
    except YAMLError as e:
        raise DefinitionError([Diagnostic(1, f"YAML syntax error: {e}")]) from None


def dump_yaml(data) -> str:
    out = io.StringIO()
    yaml_rt().dump(data, out)
    return out.getvalue()


def plain(node):
    """Converts ruamel containers and scalars to plain Python values."""
    if isinstance(node, Mapping):
        return {str(k): plain(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [plain(v) for v in node]
    if isinstance(node, bool) or node is None:
        return node
    for base in (int, float, str):
        if isinstance(node, base):
            return base(node)
    return node


def line_of(root, loc) -> int:
    """The 1-based line of the deepest key of `loc` that exists in a ruamel tree."""
    line, node = 1, root
    for part in loc:
        try:
            if isinstance(node, CommentedMap):
                key = part if part in node else str(part).replace("_", "-")
                if key not in node:
                    break
                line = node.lc.key(key)[0] + 1
                node = node[key]
            elif isinstance(node, CommentedSeq) and isinstance(part, int) and part < len(node):
                line = node.lc.item(part)[0] + 1
                node = node[part]
            else:
                break
        except (AttributeError, KeyError, TypeError, IndexError):
            break
    return line


# schema -------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FetchOptions(_Strict):
    backend: Literal["http", "browser"] = "http"
    impersonate: str = "chrome"
    method: str = "GET"
    headers: dict[str, str] | None = None
    cookies: dict[str, str] | None = None
    charset: str = "auto"
    proxy: str | None = None
    timeout: float = 30
    retry: int | bool = 2
    wait_for: str | None = None
    scroll: int = 0


class SelectorMap(_Strict):
    css: str | None = None
    xpath: str | None = None
    jmes: str | None = None
    many: bool = False
    strip: bool = False
    regex: str | None = None
    parse: Literal["date", "int", "text"] | None = None
    default: Any = None


class Pages(_Strict):
    include_source: bool = Field(True, alias="include-source")
    path: str | None = None
    start: int = 2
    end: int | None = None
    increment: int = 1
    delay: float | None = None
    next: str | SelectorMap | None = None
    max: int | None = None


class Output(_Strict):
    format: Literal["rss", "atom", "json-feed", "json"] = "rss"
    limit: int | None = Field(None, ge=0)


class WebSub(_Strict):
    enabled: bool = True
    partition: int | None = Field(None, ge=1)


class FullText(_Strict):
    enabled: bool = True
    field: Literal["summary", "content"] = "summary"
    limit: int | None = Field(None, ge=0)


class HistoryStep(_Strict):
    key: str = "link"
    keep: int | None = Field(None, ge=1)
    keep_days: float | None = Field(None, gt=0)


class WordFilterStep(_Strict):
    name: str = "default"
    fields: list[str] = ["title"]


class ExcludeStep(_Strict):
    field: str = "title"
    regex: str

    @field_validator("regex")
    @classmethod
    def _regex(cls, value):
        try:
            re.compile(value)
        except re.error as e:
            raise ValueError(f"invalid regular expression: {e}") from None
        return value


class FilterStep(_Strict):
    history: HistoryStep | bool | None = None
    dedupe: str | None = None
    wordfilter: WordFilterStep | str | None = None
    exclude: ExcludeStep | None = None
    limit: int | None = Field(None, ge=0)

    @field_validator("history", mode="before")
    @classmethod
    def _history(cls, value):
        return {} if value is True else value

    @field_validator("wordfilter", mode="before")
    @classmethod
    def _wordfilter(cls, value):
        return {"name": value} if isinstance(value, str) else value


class FeedDefinition(BaseModel):
    """A feed definition (feed.yaml). Unknown top-level keys are kept as extra fields for extractors."""

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(None, pattern=r"^[0-9a-f]{32}$", description=(
        "Optional, 32 lowercase hex characters. With an id the stored state of the feed (output, history, "
        "subscriptions) is kept under it instead of the suffix, so it survives a change of the suffix"))
    title: str | None = None
    suffix: str | None = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]*$", description=(
        "The feed is served at /feed/<suffix> and, without an id, its stored state is kept under this name. "
        "ASCII letters, digits, dot, dash, underscore and tilde. Defaults to the directory name"))
    enabled: bool = True
    tags: list[str] = []
    extends: str | None = Field(None, description="A YAML template under lib/ to inherit from")
    source: str | list[str] | None = None
    mode: Literal["selectors", "auto"] | None = None
    fetch: FetchOptions = FetchOptions()
    selectors: dict[str, str | SelectorMap] | None = None
    pages: Pages | None = None
    filter: list[FilterStep] | None = None
    fulltext: FullText | None = None
    transform: str | None = None
    output: Output = Output()
    tasks: list[str] = []
    parallel: bool = True
    websub: WebSub | None = None
    extractor: str | None = None
    params: Any = None
    expect_empty: bool = False

    @field_validator("output", mode="before")
    @classmethod
    def _output(cls, value):
        if value == "edn":
            raise ValueError("EDN output is no longer supported: use json")
        return {"format": value} if isinstance(value, str) else value

    @field_validator("websub", mode="before")
    @classmethod
    def _websub(cls, value):
        if isinstance(value, bool):
            return {"enabled": value}
        return value

    @field_validator("fulltext", mode="before")
    @classmethod
    def _fulltext(cls, value):
        if isinstance(value, bool):
            return {"enabled": value}
        return value

    @field_validator("selectors")
    @classmethod
    def _selectors(cls, value):
        for name, spec in (value or {}).items():
            raw = spec.model_dump(exclude_defaults=True) if isinstance(spec, SelectorMap) else spec
            try:
                sel.compile_part(raw)
            except sel.SelectorError as e:
                raise ValueError(f"{name}: {e}") from None
        return value


def json_schema() -> dict:
    schema = FeedDefinition.model_json_schema(by_alias=True)
    schema["title"] = "Feedxcavator feed definition"
    return schema


def normalize(raw: dict) -> dict:
    """Rewrites the 3.0.0 spellings into the current fields (FD2-5, FD2-10)."""
    data = dict(raw)
    if "task" in data:
        task = data.pop("task")
        if task and not data.get("tasks"):
            data["tasks"] = [task] if isinstance(task, str) else list(task)
    if isinstance(data.get("tasks"), str):
        data["tasks"] = [data["tasks"]]
    if isinstance(data.get("tags"), str):
        data["tags"] = [data["tags"]]
    if "realtime" in data or "partition" in data:
        realtime, partition = data.pop("realtime", None), data.pop("partition", None)
        if "websub" not in data and (realtime or partition):
            data["websub"] = {"enabled": bool(realtime), "partition": partition}
    legacy_fetch = {k: data.pop(k) for k in ("charset", "proxy", "timeout") if k in data}
    if legacy_fetch:
        fetch = dict(data.get("fetch") or {})
        for key, value in legacy_fetch.items():
            if key == "timeout" and not value:
                continue  # 0 meant "no timeout" in 3.0.0
            if value is not None:
                fetch.setdefault(key, value)
        data["fetch"] = fetch
    data.pop("group", None)  # the group is the directory now (WS2-3)
    flt = data.get("filter")
    if isinstance(flt, dict):  # the map form of 3.0.0
        steps = []
        if flt.get("history"):
            steps.append({"history": {}})
        if flt.get("content"):
            fields = [f for f in str(flt["content"]).split("+") if f]
            steps.append({"wordfilter": {"name": flt.get("wordfilter") or "default", "fields": fields}})
        data["filter"] = steps
    return data


def deep_merge(base, over):
    """FD2-6: maps merge, other values replace."""
    if isinstance(base, dict) and isinstance(over, dict):
        merged = dict(base)
        for key, value in over.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return over


def resolve_extends(data: dict, load_template, _seen=()) -> dict:
    """Merges the definition over its template chain. `load_template(name)` returns a plain dict."""
    name = data.get("extends")
    if not name:
        return data
    name = str(name)
    if name in _seen:
        raise ValueError(f"cyclic 'extends': {' -> '.join((*_seen, name))}")
    template = load_template(name)
    template = resolve_extends(normalize(template), load_template, (*_seen, name))
    template.pop("id", None)
    merged = deep_merge(template, data)
    merged.pop("extends", None)
    return merged


def _message(error: dict) -> str:
    loc = ".".join(str(part) for part in error["loc"])
    message = error["msg"].removeprefix("Value error, ")
    if error["type"] == "extra_forbidden":
        message = "unknown key"
    return f"{loc}: {message}" if loc else message


def validate(raw, tree=None, load_template=None) -> dict:
    """Validates a parsed definition and returns the normalized plain dict with defaults filled in.
    Raises DefinitionError with line numbers taken from `tree` (the ruamel document)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DefinitionError([Diagnostic(1, "a feed definition must be a mapping")])
    try:
        data = normalize(raw)
        if load_template is not None:
            data = resolve_extends(data, load_template)
    except (ValueError, OSError) as e:
        raise DefinitionError([Diagnostic(line_of(tree, ("extends",)), f"extends: {e}")]) from None
    try:
        model = FeedDefinition.model_validate(data)
    except ValidationError as e:
        raise DefinitionError(
            [Diagnostic(line_of(tree, err["loc"]), _message(err)) for err in e.errors(include_url=False)]
        ) from None
    result = model.model_dump(by_alias=False, exclude_none=False)
    # selector maps go back to plain dicts without defaults, so they compile and cache well
    if model.selectors:
        result["selectors"] = {
            name.replace("-", "_"): (spec.model_dump(exclude_defaults=True) if isinstance(spec, SelectorMap) else spec)
            for name, spec in model.selectors.items()
        }
    if model.pages and isinstance(model.pages.next, SelectorMap):
        result["pages"]["next"] = model.pages.next.model_dump(exclude_defaults=True)
    if result.get("filter"):
        result["filter"] = [{k: v for k, v in step.items() if v is not None} for step in result["filter"]]
    if result["mode"] is None and result.get("selectors"):
        result["mode"] = "selectors"
    return result


_ID_LINE = re.compile(r"""^id:[ 	]*["']?([0-9a-f]{32})["']?[ 	]*(?:#.*)?$""", re.M)


def parse_definition(text: str, load_template=None) -> tuple[dict, object]:
    """Parses and validates feed.yaml text. Returns (normalized dict, ruamel tree)."""
    tree = load_yaml(text)
    raw = plain(tree) if tree is not None else {}
    if isinstance(raw, dict) and "id" in raw and not isinstance(raw["id"], str):
        # an unquoted id made of digits is a YAML number: take it from the text as written
        match = _ID_LINE.search(text)
        if match:
            raw["id"] = match.group(1)
    return validate(raw, tree, load_template), tree


def read_identity(text: str) -> dict:
    """The id, suffix, title and tasks of a definition that does not validate, as far as they can be read."""
    identity = {}
    match = _ID_LINE.search(text)
    if match:
        identity["id"] = match.group(1)
    try:
        raw = plain(load_yaml(text))
    except DefinitionError:
        return identity
    if isinstance(raw, dict):
        raw = normalize(raw)
        if isinstance(raw.get("id"), str) and ID_RE.fullmatch(raw["id"]):
            identity["id"] = raw["id"]
        for key in ("suffix", "title"):
            if isinstance(raw.get(key), str):
                identity[key] = raw[key]
        if isinstance(raw.get("tasks"), list):
            identity["tasks"] = [str(t) for t in raw["tasks"]]
    return identity


def diagnose(text: str, load_template=None) -> list[Diagnostic]:
    try:
        parse_definition(text, load_template)
    except DefinitionError as e:
        return e.diagnostics
    return []


def set_suffix(text: str, suffix: str) -> str:
    """Sets the top-level `suffix` of a definition, leaving the rest of the text untouched."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = re.match(r"suffix:[ \t]*[^#\r\n]*?([ \t]+#[^\r\n]*)?(\r?\n)?$", line)
        if match:
            lines[index] = f"suffix: {suffix}{match.group(1) or ''}{match.group(2) or ''}"
            return "".join(lines)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    # no suffix yet (it defaulted to the directory name): add it after the title, else as the first key
    position = next((i + 1 for i, line in enumerate(lines) if line.startswith("title:")), None)
    if position is None:
        position = next((i for i, line in enumerate(lines)
                         if line.strip() and not line.lstrip().startswith("#") and line.strip() != "---"),
                        len(lines))
    lines.insert(position, f"suffix: {suffix}\n")
    return "".join(lines)


# the Feed object ------------------------------------------------------------------


class Feed(Mapping):
    """A read-only feed (UC2-19): fields are available as attributes and by key, extra fields included.
    A missing field reads as None."""

    __slots__ = ("_data",)

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", dict(data))

    def __getitem__(self, key):
        key = str(getattr(key, "name", key))
        data = self._data
        if key in data:
            return data[key]
        alt = key.replace("-", "_")
        if alt in data:
            return data[alt]
        alt = key.replace("_", "-")
        if alt in data:
            return data[alt]
        raise KeyError(key)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        try:
            return self[name]
        except KeyError:
            return None

    def __setattr__(self, name, value):
        raise TypeError("a feed is read-only: use feed.replace(...) to derive a changed copy")

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def replace(self, **changes) -> "Feed":
        """A copy of the feed with some fields changed."""
        return Feed({**self._data, **changes})

    @property
    def sources(self) -> list[str]:
        source = self._data.get("source")
        if not source:
            return []
        return [source] if isinstance(source, str) else list(source)

    @property
    def source_url(self) -> str | None:
        sources = self.sources
        return sources[0] if sources else None

    def as_dict(self) -> dict:
        return dict(self._data)

    def __repr__(self):
        return f"<Feed {self._data.get('suffix') or self._data.get('id')}>"


def make_feed(definition: dict, path: str) -> Feed:
    """Builds the runtime Feed from a validated definition and the feed path relative to feeds/."""
    data = dict(definition)
    name = path.rsplit("/", 1)[-1]
    data["path"] = path
    data["group"] = path.rsplit("/", 1)[0] if "/" in path else ""
    if not data.get("suffix"):
        data["suffix"] = name
    data["suffix"] = str(data["suffix"])
    data["uuid"] = data.get("id")
    data["key"] = data.get("id") or data["suffix"]  # what the stored state of the feed is kept under
    if not data.get("title"):
        data["title"] = data["suffix"]
    return Feed(data)
