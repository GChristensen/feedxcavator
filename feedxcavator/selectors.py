"""The selector engine.

A selector is CSS by default, XPath with the `xpath:` prefix, or JMESPath with the `jmes:` prefix (JSON
documents). CSS selectors may end with `::text`, `::attr(name)`, or the 3.0.0 attribute list `::a||b`.
"""

from __future__ import annotations

import functools
import html as _html
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit

import jmespath
import lxml.etree
import lxml.html
from cssselect import SelectorError as _CssError
from parsel import Selector
from parsel.csstranslator import HTMLTranslator

KNOWN_FIELDS = (
    "title", "link", "summary", "content", "author", "category", "image", "image_type", "guid", "published",
)
DEFAULT_ATTRS = {"link": ("href",), "image": ("src",)}
URL_FIELDS = ("link", "image")
_ATTR_FN = re.compile(r"attr\(\s*([^)\s]+)\s*\)")
_ATTR_NAME = re.compile(r"[\w:.-]+")
_translator = HTMLTranslator()


class SelectorError(ValueError):
    pass


@dataclass(frozen=True)
class Compiled:
    kind: str  # css | xpath | jmes | root
    expr: str
    getter: str = "html"  # html | text | attrs | raw
    attrs: tuple = ()
    program: object = None


@functools.lru_cache(maxsize=4096)
def compile_selector(spec: str, kind: str | None = None) -> Compiled:
    if not isinstance(spec, str) or not spec.strip():
        raise SelectorError("empty selector")
    s = spec.strip()
    if kind is None:
        if s.startswith("xpath:"):
            kind, s = "xpath", s[6:].strip()
        elif s.startswith("jmes:"):
            kind, s = "jmes", s[5:].strip()
        else:
            kind = "css"
    if kind == "xpath":
        try:
            lxml.etree.XPath(s)
        except lxml.etree.XPathError as e:
            raise SelectorError(f"invalid XPath '{s}': {e}") from None
        return Compiled("xpath", s)
    if kind == "jmes":
        try:
            program = jmespath.compile(s) if s not in ("", "@", ":root") else None
        except jmespath.exceptions.JMESPathError as e:
            raise SelectorError(f"invalid JMESPath '{s}': {e}") from None
        return Compiled("jmes" if program else "root", s, program=program)
    if s[:1] in "[{" or s.startswith("#{"):
        if not re.match(r"\[[\w-]+", s):  # `[attr]` is ordinary CSS
            raise SelectorError(
                f"'{s}' looks like a native Enlive selector, which is no longer supported: use CSS or 'xpath:'"
            )

    head, getter, attrs = s, "html", ()
    if "::" in s:
        head, tail = s.rsplit("::", 1)
        if "::" in head:  # several pseudo-elements, e.g. `h2::text, h3::text`: left to parsel
            head, getter = s, "raw"
        elif tail == "text":
            getter = "text"
        else:
            names = []
            for part in tail.split("||"):
                part = part.strip()
                m = _ATTR_FN.fullmatch(part)
                if m:
                    names.append(m.group(1))
                elif _ATTR_NAME.fullmatch(part):
                    names.append(part)
                else:
                    raise SelectorError(f"invalid attribute list '::{tail}' in selector '{s}'")
            getter, attrs = "attrs", tuple(names)
    head = head.strip()
    if head in (":root", ""):
        return Compiled("root", "", getter, attrs)
    try:
        _translator.css_to_xpath(head)
    except (_CssError, Exception) as e:  # noqa: BLE001 - cssselect raises several unrelated types
        raise SelectorError(f"invalid CSS selector '{head}': {e}") from None
    return Compiled("css", head, getter, attrs)


def as_doc(doc):
    """Accepts a parsel selector, a response, an HTML string, or parsed JSON."""
    if isinstance(doc, (Selector, list, dict)) or doc is None:
        return doc
    if isinstance(doc, (str, bytes)):
        if isinstance(doc, bytes):
            doc = doc.decode("utf-8", "replace")
        return Selector(text=doc)
    selector = getattr(doc, "selector", None)
    if isinstance(selector, Selector):
        return selector
    text = getattr(doc, "text", None)
    if isinstance(text, str):
        return Selector(text=text)
    raise TypeError(f"cannot select from {type(doc).__name__}")


def select(node, compiled: Compiled) -> list:
    if node is None:
        return []
    if compiled.kind == "root":
        return [node]
    if compiled.kind == "jmes":
        if isinstance(node, Selector):
            raise SelectorError("a 'jmes:' selector needs a JSON document")
        found = compiled.program.search(node)
        if found is None:
            return []
        return found if isinstance(found, list) else [found]
    if not isinstance(node, Selector):
        raise SelectorError(f"a {compiled.kind} selector needs an HTML or XML document")
    return list(node.css(compiled.expr) if compiled.kind == "css" else node.xpath(compiled.expr))


def _is_element(sel) -> bool:
    return isinstance(sel, Selector) and not isinstance(sel.root, str)


def inner_html(node) -> str | None:
    """Inner HTML of an element, or None when it is blank."""
    node = _first(node)
    if node is None:
        return None
    if not _is_element(node):
        return node.get() if isinstance(node, Selector) else str(node)
    el = node.root
    parts = [_html.escape(el.text, quote=False)] if el.text else []
    for child in el:
        if node.type == "xml":
            parts.append(lxml.etree.tostring(child, encoding="unicode"))
        else:
            parts.append(lxml.html.tostring(child, encoding="unicode"))
    content = "".join(parts)
    return content if content.strip() else None


def outer_html(node) -> str | None:
    """Outer HTML of an element."""
    node = _first(node)
    if node is None:
        return None
    return node.get() if isinstance(node, Selector) else str(node)


def text(node) -> str | None:
    """Text content of an element (or of an HTML string), stripped."""
    node = _first(node)
    if node is None:
        return None
    if isinstance(node, str):
        if "<" not in node:
            return node.strip()
        node = Selector(text=node)
    if not isinstance(node, Selector):
        return str(node)
    if not _is_element(node):
        return str(node.root).strip()
    return (node.xpath("string(.)").get() or "").strip()


def attr(node, name: str) -> str | None:
    """Value of an attribute of an element."""
    node = _first(node)
    if isinstance(node, str):
        node = _first(Selector(text=node).xpath("/html/body/*"))
    if not _is_element(node):
        return None
    return node.root.get(getattr(name, "name", name))


def _first(node):
    if isinstance(node, (list, tuple)):  # also parsel's SelectorList
        return node[0] if node else None
    return node


def _value(match, compiled: Compiled, default_attrs=(), text_mode=False):
    if not isinstance(match, Selector):  # a JSON value
        return match
    if not _is_element(match):
        return str(match.root)
    if compiled.getter == "raw":
        return match.get()
    attrs = compiled.attrs or default_attrs
    if attrs and compiled.getter in ("attrs", "html"):
        for name in attrs:
            value = match.root.get(name)
            if value is not None and value.strip():
                return value
        return None
    if compiled.getter == "text" or text_mode:
        return text(match) or None
    return inner_html(match)


def q(node, selector: str) -> list:
    """All matches of a selector: elements, or strings for `::text`, `::attr()` and text XPath results."""
    compiled = compile_selector(selector)
    matches = select(as_doc(node), compiled)
    if compiled.getter == "html" and compiled.kind != "jmes":
        return [m if _is_element(m) else _value(m, compiled) for m in matches]
    return [v for v in (_value(m, compiled) for m in matches) if v is not None]


def q1(node, selector: str):
    """The first match of a selector, or None."""
    found = q(node, selector)
    return found[0] if found else None


# parts ------------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    compiled: Compiled
    many: bool = False
    strip: bool = False
    regex: object = None
    parse: str | None = None
    default: object = None


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze(v)) for k, v in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def compile_part(spec) -> Part:
    if isinstance(spec, Part):
        return spec
    if isinstance(spec, str):
        return _compile_part_str(spec)
    if isinstance(spec, dict):
        return _compile_part_map(_freeze(spec))
    raise SelectorError(f"a selector must be a string or a map, got {type(spec).__name__}")


@functools.lru_cache(maxsize=4096)
def _compile_part_str(spec: str) -> Part:
    return Part(compile_selector(spec))


@functools.lru_cache(maxsize=4096)
def _compile_part_map(frozen: tuple) -> Part:
    spec = dict(frozen)
    kinds = [k for k in ("css", "xpath", "jmes") if spec.get(k)]
    if len(kinds) != 1:
        raise SelectorError("a selector map needs exactly one of 'css', 'xpath', 'jmes'")
    unknown = set(spec) - {"css", "xpath", "jmes", "many", "strip", "regex", "parse", "default"}
    if unknown:
        raise SelectorError(f"unknown selector option(s): {', '.join(sorted(unknown))}")
    kind = kinds[0]
    compiled = compile_selector(spec[kind], None if kind == "css" else kind)
    regex = None
    if spec.get("regex"):
        try:
            regex = re.compile(spec["regex"], re.S)
        except re.error as e:
            raise SelectorError(f"invalid regex '{spec['regex']}': {e}") from None
    parse = spec.get("parse")
    if parse not in (None, "date", "int", "text"):
        raise SelectorError(f"unknown 'parse' value '{parse}': expected date, int or text")
    default = spec.get("default")
    return Part(compiled, bool(spec.get("many")), bool(spec.get("strip")), regex, parse,
                list(default) if isinstance(default, tuple) else default)


def parse_date(value) -> datetime | None:
    """Parses a date in almost any format into a timezone-aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    parsed = None
    if re.fullmatch(r"\d{10}(\d{3})?", value):
        return datetime.fromtimestamp(int(value[:10])).astimezone()
    try:
        from dateutil import parser as _dateutil

        parsed = _dateutil.parse(value)
    except (ValueError, OverflowError):
        try:
            import dateparser

            parsed = dateparser.parse(value)
        except Exception:  # noqa: BLE001
            parsed = None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _post(value, part: Part):
    if value is None:
        return None
    if part.regex is not None:
        m = part.regex.search(str(value))
        if not m:
            return None
        value = m.group(1) if m.groups() else m.group(0)
    if part.parse == "text":
        value = text(str(value))
    if part.strip and isinstance(value, str):
        value = value.strip()
    if part.parse == "date":
        return parse_date(value)
    if part.parse == "int":
        digits = re.sub(r"[^\d-]", "", str(value))
        try:
            return int(digits)
        except ValueError:
            return None
    return value


def fix_relative_url(url, base):
    """Makes a link absolute against the source URL. Protocol-relative URLs take the scheme of
    the source."""
    if url is None or not str(url).strip():
        return None
    url = str(url).strip()
    if not base:
        return url
    if url.startswith("//"):
        return f"{urlsplit(base).scheme or 'http'}:{url}"
    if re.match(r"(?i)[a-z][a-z0-9+.-]*://", url) or re.match(r"(?i)(data|mailto|magnet|tg|javascript):", url):
        return url
    return urljoin(base, url)


def extract_part(node, name: str, spec, base_url=None, text_mode=False):
    part = compile_part(spec)
    matches = select(node, part.compiled)
    if not part.many:
        matches = matches[:1]
    values = []
    for match in matches:
        value = _post(_value(match, part.compiled, DEFAULT_ATTRS.get(name, ()), text_mode), part)
        if name in URL_FIELDS and isinstance(value, str):
            value = fix_relative_url(value, base_url)
        if value is not None and not (isinstance(value, str) and not value.strip()):
            values.append(value)
    if part.many:
        return values or (part.default if part.default is not None else [])
    return values[0] if values else part.default


def _field_name(name) -> str:
    return str(getattr(name, "name", name)).replace("-", "_")


def _is_blank(headline: dict) -> bool:
    for key, value in headline.items():
        if key == "html":
            continue
        if isinstance(value, str):
            if value.strip():
                return False
        elif value not in (None, [], {}):
            return False
    return True


def extract(node, base_url=None, text_mode=False, **parts) -> dict | None:
    """Builds one headline from an item node. Each keyword is a part name with its selector, evaluated
    by the rules of the YAML `selectors` block."""
    node = as_doc(node)
    headline = {}
    for name, spec in parts.items():
        name = _field_name(name)
        value = extract_part(node, name, spec, base_url, text_mode)
        if value is not None:
            headline[name] = value
    if _is_blank(headline):
        return None  # an item without title, link and summary is dropped
    if isinstance(node, Selector):
        headline["html"] = node
    return headline


def _base_of(feed, base_url):
    if base_url:
        return base_url
    source = feed.get("source") if feed is not None else None
    if isinstance(source, (list, tuple)):
        source = source[0] if source else None
    return source


def apply_selectors(doc, feed=None, base_url=None, text_mode=False) -> list | None:
    """Applies the `selectors` of a feed (the current one by default) to a document and returns the
    headlines, as the built-in extractor does. Returns None when the feed has no `item` selector."""
    from feedxcavator import context

    if feed is None:
        feed = context.current_feed.get()
    selectors = feed if isinstance(feed, dict) and "item" in feed else (feed.get("selectors") if feed else None)
    if not selectors or not selectors.get("item"):
        return None
    doc = as_doc(doc)
    if doc is None:
        return None
    base = _base_of(feed, base_url)
    item = compile_part(selectors["item"])
    parts = {name: spec for name, spec in selectors.items() if name != "item" and spec}
    headlines = []
    for node in select(doc, item.compiled):
        headline = extract(node, base, text_mode, **parts)
        if headline is not None:
            headlines.append(headline)
    return headlines


class SelectorSet:
    """A reusable set of part selectors (the result of `defselectors`). Calling it returns headlines."""

    def __init__(self, selectors: dict):
        self.selectors = {_field_name(k): v for k, v in selectors.items()}
        for spec in self.selectors.values():
            compile_part(spec)

    def __call__(self, doc, base_url=None):
        from feedxcavator import context

        return apply_selectors(doc, {"selectors": self.selectors,
                                     "source": base_url or _base_of(context.current_feed.get(), None)})


def enlive_to_css(vector) -> str:
    """Converts an Enlive-style vector of keywords to CSS: [:div.post :> :a] is `div.post > a`."""
    tokens = []
    for item in vector:
        if isinstance(item, (list, tuple)):
            raise SelectorError("nested Enlive selectors are not supported: write the selector as CSS")
        token = str(getattr(item, "name", item)).lstrip(":")
        tokens.append(">" if token in (">", "") else token)
    return " ".join(tokens)


def validate_selectors(selectors: dict) -> list[tuple[str, str]]:
    """Returns (part name, message) for every selector that does not compile."""
    problems = []
    for name, spec in (selectors or {}).items():
        try:
            compile_part(spec)
        except SelectorError as e:
            problems.append((name, str(e)))
    return problems
