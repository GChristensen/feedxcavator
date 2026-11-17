"""Extraction flow: the built-in extractor, pagination, full text, and the run modes of EX-4."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from parsel import Selector

from feedxcavator import applog, context, fetch, filters, output, selectors as sel, storage

DEFAULT_EXTRACTOR_NAME = "default-background-extractor"


@dataclass
class Result:
    output: dict | None = None  # {"output", "content_type"}
    headlines: list = field(default_factory=list)
    stored: bool = False
    error: str | None = None
    http_status: int | None = None


# documents ------------------------------------------------------------------------


def _wants_json(feed) -> bool:
    selectors = feed.get("selectors") or {}
    item = selectors.get("item")
    spec = item.get("jmes") if isinstance(item, dict) else item
    return bool(isinstance(item, dict) and item.get("jmes")) or (isinstance(spec, str) and spec.startswith("jmes:"))


def fetch_document(feed, url, useragent=None):
    """Fetches a source of a feed with its `fetch` options and parses it: a parsel selector for HTML
    and XML, parsed JSON when the item selector is JMESPath. Returns None on failure."""
    opts = feed.get("fetch") or {}
    if opts.get("backend") == "browser":
        from feedxcavator import browser

        context.fetch_state().reset()
        with browser.browser() as b:
            page = b.open(url, wait_for=opts.get("wait_for"), scroll=opts.get("scroll") or 0,
                          timeout=opts.get("timeout") or 30)
            return Selector(text=page.html())
    headers = dict(opts.get("headers") or {})
    if useragent:
        headers["User-Agent"] = useragent
    response = fetch.fetch_url(
        url,
        method=opts.get("method") or "GET",
        headers=headers or None,
        cookies=opts.get("cookies"),
        retry=opts.get("retry", 2),
        timeout=(opts.get("timeout") or 30) * 1000,
    )
    if response is None:
        return None
    text = response.text
    try:
        if _wants_json(feed):
            return json.loads(text)
        content_type = (response.content_type or "").lower()
        if "xml" in content_type and "html" not in content_type:
            document = Selector(text=text, type="xml")
            document.remove_namespaces()
            return document
        return Selector(text=text)
    except Exception as e:  # noqa: BLE001
        context.fetch_state().conversion_error = e
        return None


def parse_html_page(feed, url=None, useragent=None):
    """Fetches a page (the feed source by default) and applies the feed selectors to it.
    Returns the headlines, or None when the page cannot be fetched."""
    url = url or feed.source_url
    document = fetch_document(feed, url, useragent)
    if document is None:
        return None
    if feed.get("mode") == "auto":
        from feedxcavator import auto

        return auto.extract_from_document(feed, url, document)
    return sel.apply_selectors(document, feed, base_url=url)


def _page_url(source: str, path, n: int) -> str:
    if callable(path):
        return source + path(n)
    return source + str(path).replace("{n}", str(n)).replace("%n", str(n))


def parse_page_range(feed, path, start, end, include_source=True, increment=1, parser=None, delay=None,
                     useragent=None, source=None):
    """Reads several pages and concatenates their headlines (FD-16, FD-17). `path` is a template
    appended to the source, with `{n}` (or `%n`) replaced by the page number, or a function of it."""
    parser = parser or parse_html_page
    start = 2 if start is None else start
    increment = increment or 1
    include_source = True if include_source is None else include_source
    source = source or feed.source_url
    kwargs = {"useragent": useragent} if useragent else {}
    headlines, fetched_any = [], False
    if include_source:
        page = parser(feed, source, **kwargs)
        fetched_any = page is not None
        headlines.extend(page or [])
    for n in range(start, (end or 0) * increment + 1, increment):
        if delay:
            time.sleep(delay)
        context.check_cancelled()
        page = parser(feed, _page_url(source, path, n), **kwargs)
        fetched_any = fetched_any or page is not None
        headlines.extend(page or [])
    return headlines if fetched_any else None


def parse_pages(feed, path, n_pages, include_source=True, increment=1, parser=None, delay=None, useragent=None):
    """Reads `n_pages` pages: the source and then pages 2..n, or pages 1..n without the source."""
    return parse_page_range(feed, path, 2 if include_source else 1, n_pages, include_source=include_source,
                            increment=increment, parser=parser, delay=delay, useragent=useragent)


def _follow_next(feed, source, pages):
    """FD2-9: follows the `next` link for up to `max` pages."""
    headlines, url, seen = [], source, set()
    fetched_any = False
    for index in range(max(1, pages.get("max") or 1)):
        if url in seen:
            break
        seen.add(url)
        if index and pages.get("delay"):
            time.sleep(pages["delay"])
        context.check_cancelled()
        document = fetch_document(feed, url)
        if document is None:
            break
        fetched_any = True
        headlines.extend(sel.apply_selectors(document, feed, base_url=url) or [])
        link = sel.extract_part(document, "link", pages["next"], url)
        if not link or not isinstance(link, str):
            break
        url = link
    return headlines if fetched_any else None


def default_feed_parser(feed):
    """The body of the built-in extractor: fetches every source (and its pages) and applies the
    selectors, or the automatic extraction with `mode: auto`. Returns None when nothing could be read."""
    pages = feed.get("pages")
    headlines, fetched_any = [], False
    for source in feed.sources:
        if pages and pages.get("next"):
            part = _follow_next(feed, source, pages)
        elif pages and pages.get("path"):
            part = parse_page_range(feed, pages["path"], pages.get("start"), pages.get("end"),
                                    include_source=pages.get("include_source", True),
                                    increment=pages.get("increment"), delay=pages.get("delay"), source=source)
        else:
            part = parse_html_page(feed, source)
        if part is not None:
            fetched_any = True
            headlines.extend(part)
    return headlines if fetched_any else None


def error_headlines() -> list[dict]:
    """EX-3."""
    state = context.fetch_state()
    if state.network_error is not None:
        summary = "Network error."
    elif state.http_error is not None:
        summary = f"HTTP error: {state.http_error}"
    else:
        summary = "Probably feed selectors are out of sync with the source markup."
    return [{"title": "Error", "summary": summary}]


# post-processing ----------------------------------------------------------------------


def apply_fulltext(feed, headlines):
    """AUTO-3: replaces the summary (or fills `content`) with the main text of the linked page."""
    options = feed.get("fulltext")
    if not options or not options.get("enabled"):
        return headlines
    import trafilatura

    target = options.get("field") or "summary"
    limit = options.get("limit")
    for index, headline in enumerate(headlines):
        if limit is not None and index >= limit:
            break
        link = headline.get("link")
        if not link:
            continue
        context.check_cancelled()
        try:
            page = fetch.fetch_url(link, as_="string")
            if not page:
                continue
            text = trafilatura.extract(page, url=link, output_format="html", include_images=True,
                                       include_links=True, include_comments=False)
            if text:
                body = Selector(text=text).xpath("/html/body")
                headline[target] = sel.inner_html(body) or text
        except context.JobCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - a page that cannot be extracted leaves the headline unchanged
            applog.write("warning", f"full text extraction failed for {link}: {e}")
    return headlines


def _resolve_function(name: str, kind: str = "transform"):
    from feedxcavator import workspace

    registry = workspace.get_registry()
    fn = registry.namespace.get(name) or registry.namespace.get(name.replace("-", "_"))
    if not callable(fn):
        raise LookupError(f"{kind} function '{name}' is not defined")
    return fn


def finish_headlines(feed, headlines, testing: bool):
    """Everything after extraction: `transform`, `post_process`, the filter steps, full text and the limit."""
    headlines = [h for h in (headlines or []) if h]
    if feed.get("transform"):
        fn = _resolve_function(feed["transform"])
        transformed = []
        for headline in headlines:
            result = fn(headline)
            if result is False:
                continue
            transformed.append(headline if result is None else result)
        headlines = transformed
    if feed.get("post_process"):
        fn = _resolve_function(feed["post_process"], "post_process")
        result = fn(headlines)
        if result is not None:
            headlines = [h for h in result if h]
    headlines = filters.filter_headlines(feed, headlines, testing=testing)
    headlines = apply_fulltext(feed, headlines)
    limit = (feed.get("output") or {}).get("limit") if isinstance(feed.get("output"), dict) else None
    if limit is not None:
        headlines = headlines[:limit]
    return headlines


# running --------------------------------------------------------------------------------


def extractor_name(feed) -> str:
    return feed.get("extractor") or DEFAULT_EXTRACTOR_NAME


def _call_extractor(feed, registry):
    """Returns (headlines, builtin). `headlines` is None when the built-in extractor could not read."""
    name = feed.get("extractor")
    if name:
        extractor = registry.find_extractor(name)
        if extractor is None:
            raise LookupError(f"extractor '{name}' is not defined")
        return extractor.fn(feed), False
    return default_feed_parser(feed), True


def publish(feed, headlines, result: dict):
    """WS-6, WS-7: pushes stored output to WebSub subscribers, in parts when `partition` is set."""
    from feedxcavator import websub

    options = feed.get("websub")
    if not options or not options.get("enabled"):
        return
    try:
        partition = options.get("partition")
        if partition:
            parts = [headlines[i:i + partition] for i in range(0, len(headlines), partition)]
            for part in reversed(parts):
                time.sleep(1)
                websub.publish_content(feed, output.produce_feed_output(feed, part))
        else:
            websub.publish_content(feed, result)
    except Exception as e:  # noqa: BLE001 - WS-8: a failed delivery must not fail the job
        with context.scope(source="websub"):
            applog.write("error", e)


def run_feed(feed, mode: str, registry=None) -> Result:
    """Extracts a feed in one of the modes of EX-4: `request` (a feed URL of a feed without tasks),
    `background` (a task) or `test` (the console)."""
    from feedxcavator import workspace

    registry = registry or workspace.get_registry()
    context.new_fetch_state()
    with context.scope(feed=feed, mode=mode):
        raw, builtin = _call_extractor(feed, registry)
        state = context.fetch_state()
        http_status = state.http_error
        if raw is None and builtin:
            error = error_headlines()
            if mode == "background":
                return Result(error=error[0]["summary"], http_status=http_status)
            return Result(output=output.produce_feed_output(feed, error), headlines=[],
                          error=error[0]["summary"], http_status=http_status)
        headlines = finish_headlines(feed, list(raw or []), testing=(mode == "test"))
        if mode == "test":
            return Result(output=output.produce_feed_output(feed, headlines), headlines=headlines)
        if not headlines:
            return Result(headlines=[], http_status=http_status)
        result = output.produce_feed_output(feed, headlines)
        if mode == "background":
            context.check_cancelled()
            storage.store_feed_output(feed["key"], result["output"], result["content_type"], len(headlines))
            publish(feed, headlines, result)
            return Result(output=result, headlines=headlines, stored=True, http_status=http_status)
        return Result(output=result, headlines=headlines, http_status=http_status)
