"""Automatic extraction: feed discovery, structured data, and detection
of the repeated item structure of a page. The last step also backs "Suggest selectors"."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlsplit

from parsel import Selector

from feedxcavator import fetch
from feedxcavator import selectors as sel

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json", "application/json")
COMMON_FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml")
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "Report", "SocialMediaPosting", "VideoObject",
                 "Product", "Recipe", "Event"}
ITEM_TAGS = ("article", "li", "div", "tr", "section", "a")
_GENERIC_CLASS = re.compile(r"^(clearfix|row|col(-\w+)*|container|wrapper|active|first|last|odd|even|\d+)$")


# step 1: an existing feed -----------------------------------------------------------


def looks_like_feed(text: str) -> bool:
    head = text.lstrip()[:600].lower()
    return head.startswith("<?xml") and ("<rss" in head or "<feed" in head or "<rdf" in head) or \
        head.startswith(("<rss", "<feed")) or ('"version"' in head and "jsonfeed.org" in head)


def discover_feed_urls(document: Selector, url: str) -> list[str]:
    urls = []
    for link in document.css('link[rel~="alternate"]'):
        if (link.attrib.get("type") or "").lower() in FEED_TYPES and link.attrib.get("href"):
            urls.append(urljoin(url, link.attrib["href"]))
    return list(dict.fromkeys(urls))


def parse_feed_text(text: str, base_url: str) -> list[dict]:
    """Normalizes an RSS, Atom or JSON feed into headlines."""
    import json

    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            return []
        headlines = []
        for item in data.get("items") or []:
            authors = item.get("authors") or ([item["author"]] if item.get("author") else [])
            headline = {
                "title": item.get("title"),
                "link": item.get("url") or item.get("external_url"),
                "summary": item.get("content_html") or item.get("summary") or item.get("content_text"),
                "image": item.get("image") or item.get("banner_image"),
                "author": (authors[0] or {}).get("name") if authors else None,
                "category": item.get("tags") or None,
                "guid": str(item["id"]) if item.get("id") else None,
                "published": item.get("date_published"),
            }
            headlines.append({k: v for k, v in headline.items() if v})
        return headlines

    import feedparser

    parsed = feedparser.parse(text)
    headlines = []
    for entry in parsed.entries:
        content = entry.get("content") or []
        image = None
        for enclosure in entry.get("enclosures") or []:
            if str(enclosure.get("type", "")).startswith("image/") and enclosure.get("href"):
                image = enclosure["href"]
                break
        if not image:
            media = entry.get("media_thumbnail") or entry.get("media_content") or []
            image = media[0].get("url") if media else None
        published = entry.get("published") or entry.get("updated")
        headline = {
            "title": entry.get("title"),
            "link": sel.fix_relative_url(entry.get("link"), base_url),
            "summary": entry.get("summary"),
            "content": content[0].get("value") if content else None,
            "author": entry.get("author"),
            "category": [t.get("term") for t in entry.get("tags") or [] if t.get("term")] or None,
            "image": image,
            "guid": entry.get("id"),
            "published": sel.parse_date(published) if published else None,
        }
        if headline.get("content") == headline.get("summary"):
            headline.pop("content")
        headlines.append({k: v for k, v in headline.items() if v})
    return headlines


def from_existing_feed(document: Selector, url: str, probe_common=True) -> list[dict]:
    candidates = discover_feed_urls(document, url)
    if probe_common and not candidates:
        root = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        candidates = [root + path for path in COMMON_FEED_PATHS]
    for candidate in candidates[:6]:
        text = fetch.fetch_url(candidate, as_="string", retry=False)
        if text and looks_like_feed(text):
            headlines = parse_feed_text(text, candidate)
            if headlines:
                return headlines
    return []


# step 2: structured data ----------------------------------------------------------------


def _types(node) -> set:
    t = node.get("@type") or node.get("type") or []
    values = t if isinstance(t, list) else [t]
    return {str(v).rsplit("/", 1)[-1] for v in values}


def _first(value):
    if isinstance(value, list):
        return _first(value[0]) if value else None
    return value


def _thing_to_headline(node: dict, url: str) -> dict | None:
    props = node.get("properties") or node
    image = _first(props.get("image") or props.get("thumbnailUrl"))
    if isinstance(image, dict):
        image = image.get("url") or (image.get("properties") or {}).get("url")
    author = _first(props.get("author"))
    if isinstance(author, dict):
        author = author.get("name") or (author.get("properties") or {}).get("name")
    link = _first(props.get("url") or props.get("mainEntityOfPage"))
    if isinstance(link, dict):
        link = link.get("@id") or link.get("url")
    headline = {
        "title": _first(props.get("headline") or props.get("name")),
        "link": sel.fix_relative_url(link, url),
        "summary": _first(props.get("description") or props.get("articleBody")),
        "image": sel.fix_relative_url(image, url) if isinstance(image, str) else None,
        "author": author if isinstance(author, str) else None,
        "published": sel.parse_date(_first(props.get("datePublished") or props.get("dateCreated"))),
    }
    headline = {k: v for k, v in headline.items() if v}
    return headline if headline.get("title") or headline.get("link") else None


def _walk(node, url, found: list):
    if isinstance(node, list):
        for child in node:
            _walk(child, url, found)
        return
    if not isinstance(node, dict):
        return
    types = _types(node)
    props = node.get("properties") or node
    if "ItemList" in types:
        for element in props.get("itemListElement") or []:
            item = element.get("item", element) if isinstance(element, dict) else element
            if isinstance(item, str):
                found.append({"link": sel.fix_relative_url(item, url)})
            elif isinstance(item, dict):
                headline = _thing_to_headline(item, url) or _thing_to_headline(element, url)
                if headline:
                    found.append(headline)
        return
    if types & ARTICLE_TYPES:
        headline = _thing_to_headline(node, url)
        if headline:
            found.append(headline)
        return
    for key in ("@graph", "mainEntity", "hasPart", "blogPost", "itemListElement"):
        if key in props:
            _walk(props[key], url, found)


def from_structured_data(html: str, url: str) -> list[dict]:
    """JSON-LD and microdata. A page describing a single thing is not a list of headlines, so at
    least two items are required."""
    import extruct

    try:
        data = extruct.extract(html, base_url=url, syntaxes=["json-ld", "microdata"], errors="ignore")
    except Exception:  # noqa: BLE001
        return []
    found: list[dict] = []
    _walk(data.get("json-ld") or [], url, found)
    if len(found) < 2:
        found = []
        _walk(data.get("microdata") or [], url, found)
    unique = list({(h.get("link"), h.get("title")): h for h in found}.values())
    return unique if len(unique) >= 2 else []


def from_opengraph(html: str, url: str) -> list[dict]:
    import extruct

    try:
        data = extruct.extract(html, base_url=url, syntaxes=["opengraph"], errors="ignore")
    except Exception:  # noqa: BLE001
        return []
    for graph in data.get("opengraph") or []:
        props = dict(graph.get("properties") or [])
        if props.get("og:title"):
            return [{k: v for k, v in {
                "title": props.get("og:title"), "link": props.get("og:url") or url,
                "summary": props.get("og:description"), "image": props.get("og:image"),
            }.items() if v}]
    return []


# step 3: the repeated structure -----------------------------------------------------------


def _classes(el) -> list[str]:
    return [c for c in (el.get("class") or "").split() if c and not _GENERIC_CLASS.match(c)
            and re.fullmatch(r"-?[_a-zA-Z][\w-]*", c)]


def _css_of(el, with_id=True) -> str:
    if with_id and el.get("id") and re.fullmatch(r"[A-Za-z][\w-]*", el.get("id")) and not re.search(r"\d{3,}", el.get("id")):
        return f"{el.tag}#{el.get('id')}"
    classes = _classes(el)[:2]
    return el.tag + "".join(f".{c}" for c in classes)


def _signature(el) -> tuple:
    return (el.tag, tuple(sorted(_classes(el))))


def _item_score(el) -> float:
    links = [a for a in el.iter("a") if a.get("href")]
    if el.tag == "a" and el.get("href"):
        links.append(el)
    if not links:
        return 0
    text = " ".join(" ".join(el.itertext()).split())
    if len(text) < 12:
        return 0
    score = min(len(text), 300) / 30
    if any(True for _ in el.iter("h1", "h2", "h3", "h4", "h5", "h6")):
        score += 6
    if any(True for _ in el.iter("img")):
        score += 2
    if any(True for _ in el.iter("time")):
        score += 3
    if len(links) > 12:
        score /= 3  # a navigation block rather than an item
    return score


def _relative_css(item, target) -> str:
    """A selector for `target` relative to `item`."""
    if target is item:
        return ":root"
    css = _css_of(target, with_id=False)
    if css == target.tag and target.tag in ("div", "span", "a", "p"):
        parent = target.getparent()
        if parent is not None and parent is not item and _classes(parent):
            return f"{_css_of(parent, with_id=False)} {target.tag}"
    return css


def _find_field(item, predicate):
    for el in item.iter():
        if isinstance(el.tag, str) and predicate(el):
            return el
    return None


def _has_class(el, pattern) -> bool:
    return bool(re.search(pattern, (el.get("class") or "") + " " + (el.get("itemprop") or ""), re.I))


def suggest_selectors(document, url: str | None = None) -> dict | None:
    """Detects the repeated item structure of a listing page and proposes a `selectors` block."""
    document = sel.as_doc(document)
    if not isinstance(document, Selector):
        return None
    root = document.root
    body = root.find(".//body")
    if body is None:
        body = root
    for junk in list(body.iter("script", "style", "noscript", "nav", "footer", "header", "aside", "form")):
        junk.drop_tree() if hasattr(junk, "drop_tree") else None

    best, best_score = None, 0.0
    merged = defaultdict(list)  # the same signature under parents of the same signature
    for parent in body.iter():
        if not isinstance(parent.tag, str):
            continue
        siblings = defaultdict(list)
        for el in parent:
            if isinstance(el.tag, str) and el.tag in ITEM_TAGS:
                siblings[_signature(el)].append(el)
        for signature, elements in siblings.items():
            if len(elements) >= 2:
                merged[(_signature(parent), signature)].extend(elements)
    for elements in merged.values():
        if len(elements) < 3:
            continue
        scores = [_item_score(el) for el in elements]
        good = [s for s in scores if s > 0]
        if len(good) < max(3, len(elements) * 0.6):
            continue
        total = (sum(good) / len(good)) * min(len(good), 40) ** 0.5
        if elements[0].tag == "article":
            total *= 1.5
        if total > best_score:
            best, best_score = elements, total
    if not best:
        return None

    sample = best[0]
    parent = sample.getparent()
    item_css = _css_of(sample, with_id=False)
    if item_css == sample.tag or len(document.css(item_css)) > len(best) * 1.3:
        prefix = _css_of(parent)
        item_css = f"{prefix} > {item_css}" if prefix != parent.tag or parent.tag not in ("div", "span") else item_css

    def most_common(finder):
        found = Counter()
        for item in best[:20]:
            target = finder(item)
            if target is not None:
                found[_relative_css(item, target)] += 1
        if not found:
            return None
        css, count = found.most_common(1)[0]
        return css if count >= min(len(best), 20) * 0.5 else None

    def title_el(item):
        heading = _find_field(item, lambda e: e.tag in ("h1", "h2", "h3", "h4", "h5", "h6"))
        if heading is not None:
            return heading
        links = [a for a in item.iter("a") if a.get("href") and " ".join(a.itertext()).strip()]
        if item.tag == "a":
            return item
        return max(links, key=lambda a: len(" ".join(a.itertext()).strip()), default=None)

    def link_el(item):
        if item.tag == "a":
            return item
        title = title_el(item)
        if title is not None:
            if title.tag == "a":
                return title
            inner = title.find(".//a")
            if inner is not None and inner.get("href"):
                return inner
            ancestor = title.getparent()
            while ancestor is not None and ancestor is not item:
                if ancestor.tag == "a":
                    return ancestor
                ancestor = ancestor.getparent()
        return _find_field(item, lambda e: e.tag == "a" and e.get("href"))

    selectors = {"item": item_css}
    title = most_common(title_el)
    if title:
        selectors["title"] = f"{title}::text"  # a title is text, not the markup of the link around it
    link_target = Counter()
    for item in best[:20]:
        target = link_el(item)
        if target is not None:
            css = _relative_css(item, target)
            title_node = title_el(item)
            if title_node is not None and target is not item and title_node is not target \
                    and target in list(title_node.iter("a")):
                css = f"{_relative_css(item, title_node)} a"
            link_target[css] += 1
    if link_target:
        selectors["link"] = link_target.most_common(1)[0][0]
    def summary_el(item):
        marked = _find_field(
            item, lambda e: e.tag in ("p", "div") and len(" ".join(e.itertext()).strip()) > 20
            and _has_class(e, r"summary|excerpt|desc|preview|teaser|lead|intro|text"))
        if marked is not None:
            return marked
        return _find_field(item, lambda e: e.tag == "p" and len(" ".join(e.itertext()).strip()) > 40)

    summary = most_common(summary_el)
    if summary and summary != title:
        selectors["summary"] = summary
    image = most_common(lambda item: _find_field(item, lambda e: e.tag == "img"))
    if image:
        lazy = next((a for a in ("data-src", "data-lazy-src", "data-original")
                     if any(img.get(a) for item in best[:10] for img in item.iter("img"))), None)
        selectors["image"] = f"{image}::attr({lazy})||src" if lazy else image
    date = most_common(lambda item: _find_field(item, lambda e: e.tag == "time"))
    if date:
        has_datetime = any(t.get("datetime") for item in best[:10] for t in item.iter("time"))
        selectors["published"] = {"css": f"{date}::attr(datetime)" if has_datetime else f"{date}::text",
                                  "parse": "date"}
    author = most_common(lambda item: _find_field(
        item, lambda e: e.tag in ("a", "span", "div") and _has_class(e, r"\bauthor|byline|user-?name")))
    if author:
        selectors["author"] = {"css": f"{author}::text", "strip": True}
    return selectors


def selectors_yaml(selectors: dict) -> str:
    """Renders a proposed `selectors` block as YAML text."""
    import json

    lines = ["selectors:"]
    for name, spec in selectors.items():
        if isinstance(spec, dict):
            body = ", ".join(f"{k}: {json.dumps(v) if isinstance(v, str) else str(v).lower()}" for k, v in spec.items())
            lines.append(f"  {name}: {{{body}}}")
        else:
            lines.append(f"  {name}: {json.dumps(spec)}")
    return "\n".join(lines) + "\n"


def from_structure(document: Selector, url: str) -> tuple[list[dict], dict | None]:
    selectors = suggest_selectors(Selector(text=document.get()), url)
    if not selectors:
        return [], None
    headlines = sel.apply_selectors(document, {"selectors": selectors, "source": url}, base_url=url) or []
    return headlines, selectors


# entry points ---------------------------------------------------------------------------


def extract_from_document(feed, url: str, document) -> list[dict]:
    """The first step that gives results wins. OpenGraph describes the page itself, so it is
    used only when nothing else was found."""
    if not isinstance(document, Selector):
        return []
    html = document.get()
    if looks_like_feed(html) or document.type == "xml":
        headlines = parse_feed_text(html, url)
        if headlines:
            return headlines
    headlines = from_existing_feed(document, url)
    if headlines:
        return headlines
    headlines = from_structured_data(html, url)
    if headlines:
        return headlines
    headlines, _ = from_structure(document, url)
    if headlines:
        return headlines
    return from_opengraph(html, url)


def suggest_for_url(url: str, fetch_options: dict | None = None) -> dict | None:
    """Fetches the source and proposes selectors for it."""
    from feedxcavator.feeddef import Feed
    from feedxcavator import context, extraction

    feed = Feed({"source": url, "fetch": fetch_options or {}})
    with context.scope(feed=feed, mode="test"):
        document = extraction.fetch_document(feed, url)
    if not isinstance(document, Selector):
        return None
    return suggest_selectors(document, url)
