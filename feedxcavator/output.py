"""Feed output: RSS 2.0, Atom, JSON Feed 1.1 and plain JSON."""

from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timedelta, timezone as _tz

from django.utils import feedgenerator
from django.utils.feedgenerator import rfc2822_date, rfc3339_date

from feedxcavator import conf
from feedxcavator.selectors import parse_date

CONTENT_TYPES = {
    "rss": "application/rss+xml",
    "atom": "application/atom+xml",
    "json-feed": "application/json",
    "json": "application/json",
}


def get_websub_url() -> str:
    return f"{conf.websub_host()}/websub"


def get_feed_url(feed) -> str:
    """The public URL of a feed."""
    if feed.get("suffix"):
        return f"{conf.websub_host()}/feed/{feed['suffix']}"
    return f"{conf.websub_host()}/feed/uuid:{feed.get('id')}"


def _websub(feed) -> bool:
    websub = feed.get("websub")
    return bool(websub and (websub is True or websub.get("enabled")))


def _source(feed) -> str:
    source = feed.get("source")
    if isinstance(source, (list, tuple)):
        source = source[0] if source else None
    return source or ""


def _categories(headline) -> list[str]:
    category = headline.get("category")
    if not category:
        return []
    if isinstance(category, str):
        return [category]
    return [str(c) for c in category if c]


def _dates(headlines, now):
    """Item dates: `published` when present, otherwise decreasing by 5 seconds from 6 seconds before now."""
    dates = []
    for index, headline in enumerate(headlines):
        published = parse_date(headline.get("published")) if headline.get("published") else None
        dates.append(published or now - timedelta(seconds=6 + 5 * index))
    return dates


def _text(value) -> str:
    return "" if value is None else str(value)


def _image_type(headline) -> str:
    return headline.get("image_type") or mimetypes.guess_type(str(headline["image"]).split("?")[0])[0] or "image/jpeg"


class _Rss(feedgenerator.Rss201rev2Feed):
    content_type = "application/rss+xml; charset=utf-8"

    def rss_attributes(self):
        attrs = super().rss_attributes()
        attrs["xmlns:content"] = "http://purl.org/rss/1.0/modules/content/"
        if self.feed.get("xml_base"):
            attrs["xml:base"] = self.feed["xml_base"]
        return attrs

    def add_root_elements(self, handler):
        handler.addQuickElement("title", self.feed["title"])
        handler.addQuickElement("link", self.feed["link"])
        handler.addQuickElement("description", self.feed["description"])
        if self.feed.get("hub"):
            handler.addQuickElement("atom:link", None, {"rel": "hub", "href": self.feed["hub"]})
        handler.addQuickElement(
            "atom:link", None, {"rel": "self", "type": "application/rss+xml", "href": self.feed["feed_url"]}
        )
        date = rfc2822_date(self.feed["now"])
        handler.addQuickElement("pubDate", date)
        handler.addQuickElement("lastBuildDate", date)

    def add_item_elements(self, handler, item):
        handler.addQuickElement("title", item["title"])
        handler.addQuickElement("link", item["link"])
        handler.addQuickElement("pubDate", rfc2822_date(item["pubdate"]))
        if item.get("unique_id"):
            handler.addQuickElement("guid", item["unique_id"], {"isPermaLink": "false"})
        if item.get("author_name"):
            handler.addQuickElement("author", item["author_name"])
        for category in item.get("categories") or ():
            handler.addQuickElement("category", category)
        handler.addQuickElement("description", item["description"])
        if item.get("content"):
            handler.addQuickElement("content:encoded", item["content"])
        for enclosure in item.get("enclosures") or ():
            handler.addQuickElement(
                "enclosure", "", {"url": enclosure.url, "length": enclosure.length, "type": enclosure.mime_type}
            )


class _Atom(feedgenerator.Atom1Feed):
    content_type = "application/atom+xml; charset=utf-8"

    def root_attributes(self):
        attrs = super().root_attributes()
        if self.feed.get("xml_base"):
            attrs["xml:base"] = self.feed["xml_base"]
        return attrs

    def add_root_elements(self, handler):
        super().add_root_elements(handler)
        if self.feed.get("hub"):
            handler.addQuickElement("link", "", {"rel": "hub", "href": self.feed["hub"]})

    def latest_post_date(self):
        return self.feed["now"]

    def add_item_elements(self, handler, item):
        super().add_item_elements(handler, item)
        if item.get("content"):
            handler.addQuickElement("content", item["content"], {"type": "html"})


def _generate_xml(feed, headlines, generator_class) -> str:
    now = datetime.now(_tz.utc)
    websub = _websub(feed)
    generator = generator_class(
        title=_text(feed.get("title")),
        link=_source(feed),
        description="",
        feed_url=get_feed_url(feed),
        xml_base=_source(feed),
        hub=get_websub_url() if websub else None,
        now=now,
    )
    for headline, date in zip(headlines, _dates(headlines, now)):
        guid = headline.get("guid") or (headline.get("link") if websub else None)
        enclosures = []
        if headline.get("image"):
            enclosures.append(feedgenerator.Enclosure(str(headline["image"]), "0", _image_type(headline)))
        generator.add_item(
            title=_text(headline.get("title")),
            link=_text(headline.get("link")),
            description=_text(headline.get("summary")),
            content=_text(headline.get("content")),
            author_name=_text(headline.get("author")) or None,
            pubdate=date,
            updateddate=date,
            unique_id=_text(guid) if guid else (None if generator_class is _Rss else _text(headline.get("link"))),
            unique_id_is_permalink=False,
            categories=_categories(headline),
            enclosures=enclosures,
        )
    return generator.writeString("utf-8")


def generate_rss(feed, headlines) -> str:
    return _generate_xml(feed, headlines, _Rss)


def generate_atom(feed, headlines) -> str:
    return _generate_xml(feed, headlines, _Atom)


def generate_json_feed(feed, headlines) -> str:
    """JSON Feed 1.1."""
    document = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": feed.get("title"),
        "home_page_url": _source(feed),
        "feed_url": get_feed_url(feed),
    }
    if _websub(feed):
        document["hubs"] = [{"type": "WebSub", "url": get_websub_url()}]
    items = []
    for headline in headlines:
        item = {"id": headline.get("guid") or headline.get("link"), "url": headline.get("link")}
        if headline.get("title"):
            item["title"] = headline["title"]
        if headline.get("summary") or headline.get("content"):
            item["content_html"] = headline.get("summary") or headline.get("content")
        if headline.get("image"):
            item["image"] = headline["image"]
        if headline.get("author"):
            item["authors"] = [{"name": headline["author"]}]
            item["author"] = {"name": headline["author"]}
        if headline.get("published"):
            published = parse_date(headline["published"])
            if published:
                item["date_published"] = rfc3339_date(published)
        if _categories(headline):
            item["tags"] = _categories(headline)
        items.append(item)
    document["items"] = items
    return json.dumps(document, ensure_ascii=False)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def serializable(headline: dict) -> dict:
    return {k: v for k, v in headline.items() if k not in ("guid", "html")}


def generate_json(feed, headlines) -> str:
    """Plain JSON: the headlines with all their fields except `guid` and `html`."""
    return json.dumps([serializable(h) for h in headlines], ensure_ascii=False, default=_json_default)


def output_format(feed) -> str:
    output = feed.get("output") or "rss"
    return output if isinstance(output, str) else (output.get("format") or "rss")


def produce_feed_output(feed, headlines) -> dict:
    """Serializes headlines in the output format of the feed.
    Returns {"output": text, "content_type": type}."""
    headlines = list(headlines or [])
    output = feed.get("output")
    limit = output.get("limit") if isinstance(output, dict) else None
    if limit is not None:
        headlines = headlines[:limit]
    fmt = output_format(feed)
    generator = {
        "rss": generate_rss, "atom": generate_atom, "json-feed": generate_json_feed, "json": generate_json,
    }.get(fmt, generate_rss)
    return {
        "output": generator(feed, headlines),
        "content_type": CONTENT_TYPES.get(fmt, CONTENT_TYPES["rss"]),
        "item_count": len(headlines),
    }


def placeholder_output(feed) -> dict:
    """The output of a feed with tasks that has not been fetched yet: a single headline saying so, so that
    the feed can be subscribed to before its first run. The guid is stable, so readers show it once."""
    headline = {
        "title": "No data yet",
        "link": _source(feed) or get_feed_url(feed),
        "guid": f"feedxcavator:no-data:{feed.get('key') or feed.get('suffix')}",
        "summary": "This feed has not been fetched yet. Its headlines will appear after the first run of its task.",
    }
    return produce_feed_output(feed, [headline])


def xml_format(xml: str | None) -> str | None:
    """Pretty-prints an XML document."""
    if not xml:
        return xml
    import lxml.etree

    try:
        root = lxml.etree.fromstring(xml.encode("utf-8"), lxml.etree.XMLParser(remove_blank_text=True))
        lxml.etree.indent(root, space="  ")
        return '<?xml version="1.0" encoding="utf-8"?>\n' + lxml.etree.tostring(root, encoding="unicode")
    except lxml.etree.XMLSyntaxError:
        return xml
