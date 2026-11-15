import json
from datetime import timedelta

import lxml.etree
from django.utils import timezone

from conftest import html
from feedxcavator import auto, extraction, feeddef, filters, output, storage, websub
from feedxcavator.models import HistoryKey, Subscription

LIST = """<html><body><div id="list">
{items}
</div><a class="next" href="{next}">next</a></body></html>"""
ITEM = '<article class="post"><h2><a href="/p/{n}">Title {n}</a></h2><p class="preview">Summary {n}</p></article>'


def page(numbers, next_="#"):
    return html(LIST.format(items="\n".join(ITEM.format(n=n) for n in numbers), next=next_))


def make_feed(source, extra=""):
    text = (f"id: {'a' * 32}\ntitle: Test feed\nsuffix: test\nsource: {source}\n"
            "selectors:\n  item: article.post\n  title: h2 a\n  link: h2 a\n  summary: p.preview\n" + extra)
    definition, _ = feeddef.parse_definition(text)
    return feeddef.make_feed(definition, "test")


def rss_items(text):
    root = lxml.etree.fromstring(text.encode())
    return root, root.findall("./channel/item")


def test_builtin_extractor_rss(http):
    http.routes["/list"] = page([1, 2, 3])
    result = extraction.run_feed(make_feed(http.url + "/list"), "request")
    assert result.output["content_type"] == "application/rss+xml"
    root, items = rss_items(result.output["output"])
    assert root.get("{http://www.w3.org/XML/1998/namespace}base") == http.url + "/list"
    channel = root.find("channel")
    assert channel.findtext("title") == "Test feed" and channel.findtext("link") == http.url + "/list"
    links = channel.findall("{http://www.w3.org/2005/Atom}link")
    assert [(l.get("rel"), l.get("href")) for l in links] == [("self", "http://feeds.test/feed/test")]
    assert channel.findtext("pubDate") and channel.findtext("lastBuildDate")
    assert [i.findtext("title") for i in items] == ["Title 1", "Title 2", "Title 3"]
    assert items[0].findtext("link") == http.url + "/p/1" and items[0].findtext("description") == "Summary 1"
    from email.utils import parsedate_to_datetime

    dates = [parsedate_to_datetime(i.findtext("pubDate")) for i in items]
    assert dates[0] - dates[1] == timedelta(seconds=5) and dates[1] - dates[2] == timedelta(seconds=5)  # EX-8
    assert items[0].find("guid") is None


def test_error_feeds(http):
    http.routes["/list"] = (500, {}, "")
    feed = make_feed(http.url + "/list", "fetch: {retry: false}\n")
    assert "HTTP error: 500" in extraction.run_feed(feed, "request").output["output"]  # EX-3
    assert "Network error." in extraction.run_feed(make_feed("http://127.0.0.1:9/x", "fetch: {retry: false, timeout: 2}\n"),
                                                    "request").output["output"]
    background = extraction.run_feed(feed, "background")
    assert background.output is None and background.error == "HTTP error: 500" and background.http_status == 500
    assert storage.fetch_feed_output(feed["id"]) is None
    http.routes["/empty"] = html("<html><body>nothing</body></html>")
    assert extraction.run_feed(make_feed(http.url + "/empty"), "request").output is None  # 204


def test_numeric_pages_and_follow_next(http):
    http.routes["/list"] = page([1])
    for n in (20, 40, 60):
        http.routes[f"/list/p{n}"] = page([n])
    feed = make_feed(http.url + "/list", "pages: {path: '/p{n}', start: 20, end: 3, increment: 20}\n")
    assert [h["title"] for h in extraction.default_feed_parser(feed)] == ["Title 1", "Title 20", "Title 40",
                                                                         "Title 60"]  # FD-17
    legacy = make_feed(http.url + "/list", "pages: {path: '/p%n', start: 20, end: 1, increment: 20, include-source: false}\n")
    assert [h["title"] for h in extraction.default_feed_parser(legacy)] == ["Title 20"]

    http.routes["/a"] = page([1], "/b")
    http.routes["/b"] = page([2], "/c")
    http.routes["/c"] = page([3], "/d")
    follow = make_feed(http.url + "/a", "pages: {next: 'a.next::attr(href)', max: 2}\n")
    assert [h["title"] for h in extraction.default_feed_parser(follow)] == ["Title 1", "Title 2"]  # FD2-9


def test_several_sources_json_and_charset(http):
    http.routes["/one"] = page([1])
    http.routes["/two"] = page([2])
    feed = make_feed(f"[{http.url}/one, {http.url}/two]")
    assert [h["link"] for h in extraction.default_feed_parser(feed)] == [http.url + "/p/1", http.url + "/p/2"]

    http.routes["/api"] = (200, {"Content-Type": "application/json"},
                           json.dumps({"items": [{"name": "J", "href": "/j"}]}))
    definition, _ = feeddef.parse_definition(
        f"source: {http.url}/api\nselectors: {{item: 'jmes:items', title: 'jmes:name', link: 'jmes:href'}}\n")
    assert extraction.default_feed_parser(feeddef.make_feed(definition, "j")) == [
        {"title": "J", "link": http.url + "/j"}]  # SEL-5

    http.routes["/cp1251"] = (200, {"Content-Type": "text/html; charset=windows-1251"},
                              ITEM.format(n=1).replace("Title", "Заголовок").encode("windows-1251"))
    assert extraction.default_feed_parser(make_feed(http.url + "/cp1251"))[0]["title"] == "Заголовок 1"


def test_filter_steps_and_history(http):
    http.routes["/list"] = page([1, 2, 2, 3, 4])
    filters.add_filter_word("title 3")
    filters.add_filter_regex("spam", r"summary\s+4")
    feed = make_feed(http.url + "/list", (
        "filter:\n  - history: {key: link, keep: 4}\n  - dedupe: link\n  - wordfilter: default\n"
        "  - wordfilter: {name: spam, fields: [title, summary]}\n  - exclude: {field: title, regex: '^Title 1$'}\n"
        "  - limit: 5\n"))
    titles = lambda mode: [h["title"] for h in extraction.run_feed(feed, mode).headlines]  # noqa: E731
    assert titles("test") == ["Title 2"]  # a test skips history (FD-20)
    assert HistoryKey.objects.count() == 0
    assert titles("request") == ["Title 2"]
    assert HistoryKey.objects.count() == 4
    assert titles("request") == []
    http.routes["/list"] = page([2, 5])
    assert titles("request") == ["Title 5"]
    assert HistoryKey.objects.count() == 4  # FD2-11: the last `keep` keys

    key_feed = "some string key"  # UC-8
    items = [{"guid": "g1", "link": "l1"}, {"guid": "g2", "link": "l2"}]
    assert len(filters.filter_history_by_guid_update(key_feed, items)) == 2
    assert filters.filter_history_by_guid_update(key_feed, items) == []
    assert len(filters.filter_history(key_feed, items)) == 2  # the link history of that key is empty

    old = filters.history_namespace("aging")
    HistoryKey.objects.create(namespace=old, key_hash="x", seen_at=timezone.now() - timedelta(days=40))
    filters.filter_history_update("aging", [{"link": "new"}], keep_days=30)
    assert list(HistoryKey.objects.filter(namespace=old).values_list("key_hash", flat=True)) != ["x"]
    assert filters.list_word_filter("spam") == [r"summary\s+4"] and filters.list_word_filters() == ["default", "spam"]


def test_output_formats():
    headlines = [
        {"title": "A <b>&", "link": "https://e.test/a", "summary": "<p>S</p>", "content": "<p>full</p>",
         "author": "Ann", "category": ["x", "y"], "image": "https://e.test/a.png", "guid": "g-a",
         "published": "2024-05-01T10:00:00+00:00", "html": object(), "extra": 1},
        {"title": "B", "link": "https://e.test/b", "category": "solo"},
    ]
    feed = feeddef.Feed({"id": "f" * 32, "title": "Out", "suffix": "out", "source": "https://e.test/",
                         "websub": {"enabled": True}, "output": {"format": "rss", "limit": None}})
    root, items = rss_items(output.produce_feed_output(feed, headlines)["output"])
    hub = [l.get("href") for l in root.find("channel").findall("{http://www.w3.org/2005/Atom}link")
           if l.get("rel") == "hub"]
    assert hub == ["http://feeds.test/websub"]
    first, second = items
    assert first.findtext("title") == "A <b>&" and first.findtext("author") == "Ann"
    assert [c.text for c in first.findall("category")] == ["x", "y"]  # once per value (fixes 17.3)
    assert first.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") == "<p>full</p>"  # OUT-1
    assert first.find("enclosure").get("type") == "image/png" and first.find("guid").text == "g-a"
    assert first.findtext("pubDate").startswith("Wed, 01 May 2024 10:00:00")  # OUT-2
    assert second.find("guid").text == "https://e.test/b"  # a WebSub feed without a guid uses the link
    assert [c.text for c in second.findall("category")] == ["solo"]

    atom = lxml.etree.fromstring(output.produce_feed_output(feed.replace(output={"format": "atom"}), headlines)[
        "output"].encode())
    ns = {"a": "http://www.w3.org/2005/Atom"}
    assert atom.findtext("a:title", namespaces=ns) == "Out" and len(atom.findall("a:entry", ns)) == 2
    assert atom.find("a:entry/a:content", ns).text == "<p>full</p>"
    assert {l.get("rel") for l in atom.findall("a:link", ns)} >= {"self", "hub", "alternate"}

    document = json.loads(output.produce_feed_output(feed.replace(output={"format": "json-feed"}), headlines)["output"])
    assert document["version"] == "https://jsonfeed.org/version/1.1" and document["hubs"][0]["type"] == "WebSub"
    assert document["items"][0]["authors"] == [{"name": "Ann"}] and document["items"][0]["content_html"] == "<p>S</p>"
    assert document["items"][0]["date_published"].startswith("2024-05-01T10:00:00")

    plain = output.produce_feed_output(feed.replace(output={"format": "json", "limit": 1}), headlines)
    data = json.loads(plain["output"])
    assert len(data) == 1 and "guid" not in data[0] and "html" not in data[0] and data[0]["extra"] == 1  # OUT-4, OUT-5
    assert plain["content_type"] == "application/json"


def test_background_stores_and_publishes(http):
    http.routes["/list"] = page([1, 2, 3])
    received = []

    def callback(handler):
        if handler.command == "GET":
            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(handler.path).query)
            return 200, {}, query["hub.challenge"][0]
        received.append((dict(handler.headers), http.requests[-1][3]))
        return 204, {}, ""

    http.routes["/callback"] = callback
    feed = make_feed(http.url + "/list", "tasks: [t]\nwebsub: {enabled: true, partition: 2}\n")
    from feedxcavator import workspace

    workspace._live.feeds["test"] = workspace.FeedInfo("test", "feeds/test", id=feed["id"], suffix="test", feed=feed)
    workspace._live.index_feeds()
    params = {"hub.mode": "subscribe", "hub.topic": "http://feeds.test/feed/uuid:" + feed["id"],
              "hub.callback": http.url + "/callback?x=1", "hub.secret": "s3cret"}
    assert websub.subscribe(params, background=False) == 202
    sub = Subscription.objects.get()
    assert sub.verified and sub.expires_at > timezone.now()  # the challenge was echoed (fixes 17.5, 17.7)
    verification = next(r for r in http.requests if r[0] == "GET" and "/callback" in r[1])
    assert verification[1].startswith("/callback?x=1&hub.mode=subscribe")

    import time as _time

    real_sleep, _time.sleep = _time.sleep, lambda s: None
    try:
        result = extraction.run_feed(feed, "background")
    finally:
        _time.sleep = real_sleep
    assert result.stored and len(result.headlines) == 3
    stored = storage.fetch_feed_output(feed["id"])
    assert stored["content_type"] == "application/rss+xml" and "Title 3" in stored["output"]
    assert len(received) == 2  # WS-7: parts of 2, the last part first
    assert b"Title 3" in received[0][1] and b"Title 1" not in received[0][1] and b"Title 1" in received[1][1]
    headers = {k.lower(): v for k, v in received[0][0].items()}
    import hashlib
    import hmac

    assert headers["x-hub-signature"] == "sha1=" + hmac.new(b"s3cret", received[0][1], hashlib.sha1).hexdigest()
    assert 'rel="self"' in headers["link"] and 'rel="hub"' in headers["link"]
    assert headers["content-type"] == "application/rss+xml; charset=utf-8"

    # a second subscriber is kept beside the first (STO2-8); a failed verification is dropped
    http.routes["/liar"] = (200, {}, "wrong")
    websub.subscribe({**params, "hub.callback": http.url + "/liar"}, background=False)
    assert Subscription.objects.count() == 1
    assert websub.hub_action({"hub.mode": "publish", "hub.url": "http://feeds.test/feed/test"}) == 204  # 17.6
    assert len(received) == 3
    Subscription.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert websub.publish_content(feed, stored) == 0 and Subscription.objects.count() == 0  # leases expire
    assert websub.unsubscribe({**params}, background=False) == 202


def test_fulltext_and_transform(http, ws):
    http.routes["/list"] = page([1, 2])
    article = ("<html><body><nav>menu</nav><article><h1>Head</h1>" +
               "".join(f"<p>Paragraph {i} of the article with enough words to count as the main text.</p>"
                       for i in range(8)) + "</article><footer>foot</footer></body></html>")
    http.routes["/p/1"] = html(article)
    http.routes["/p/2"] = (404, {}, "")
    ws({"lib/t.py": "def tag(h):\n    if h['title'].endswith('9'):\n        return False\n"
                    "    return {**h, 'title': '[x] ' + h['title']}\n"}, startup=True)
    feed = make_feed(http.url + "/list", "fulltext: true\ntransform: tag\n")
    headlines = extraction.run_feed(feed, "test").headlines
    assert headlines[0]["title"] == "[x] Title 1"
    assert "Paragraph 7" in headlines[0]["summary"] and "menu" not in headlines[0]["summary"]  # AUTO-3
    assert headlines[1]["summary"] == "Summary 2"  # a page that cannot be fetched leaves the headline unchanged
    limited = extraction.run_feed(make_feed(http.url + "/list", "fulltext: {field: content, limit: 0}\n"), "test")
    assert "content" not in limited.headlines[0]


def test_auto_mode(http):
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
           '<item><title>From feed</title><link>/f/1</link><description>d</description>'
           '<guid>g1</guid><category>c</category></item></channel></rss>')
    http.routes["/withfeed"] = html('<html><head><link rel="alternate" type="application/rss+xml" href="/rss.xml">'
                                    '</head><body></body></html>')
    http.routes["/rss.xml"] = (200, {"Content-Type": "application/rss+xml"}, rss)
    definition, _ = feeddef.parse_definition(f"source: {http.url}/withfeed\nmode: auto\n")
    headlines = extraction.default_feed_parser(feeddef.make_feed(definition, "auto"))
    assert headlines[0]["title"] == "From feed" and headlines[0]["link"] == http.url + "/f/1"  # AUTO-1 step 1
    assert headlines[0]["guid"] == "g1" and headlines[0]["category"] == ["c"]

    del http.routes["/rss.xml"]  # otherwise found as a common feed path
    ld = json.dumps({"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": {"@type": "Article", "headline": f"LD {i}", "url": f"/ld/{i}"}}
        for i in range(3)]})
    http.routes["/ld"] = html(f'<html><head><script type="application/ld+json">{ld}</script></head><body></body></html>')
    definition, _ = feeddef.parse_definition(f"source: {http.url}/ld\nmode: auto\n")
    headlines = extraction.default_feed_parser(feeddef.make_feed(definition, "auto"))
    assert [h["title"] for h in headlines] == ["LD 0", "LD 1", "LD 2"]  # step 2

    listing = "".join(
        f'<div class="card"><h3><a href="/n/{i}">Structured headline number {i}</a></h3>'
        f'<img data-src="/i/{i}.jpg" src="x.gif"><time datetime="2024-01-0{i + 1}">d</time>'
        f'<p class="excerpt">An excerpt long enough to be picked as the summary {i}.</p></div>' for i in range(5))
    http.routes["/plain"] = html(f"<html><body><nav><a href='/'>Home</a><a href='/a'>About</a></nav>"
                                 f"<main class='feed'>{listing}</main></body></html>")
    definition, _ = feeddef.parse_definition(f"source: {http.url}/plain\nmode: auto\n")
    headlines = extraction.default_feed_parser(feeddef.make_feed(definition, "auto"))
    assert len(headlines) == 5 and headlines[2]["title"].startswith("Structured headline number 2")  # step 3
    assert headlines[2]["link"] == http.url + "/n/2"

    selectors = auto.suggest_for_url(http.url + "/plain")  # AUTO-2
    assert selectors["item"].endswith("div.card") and selectors["title"] == "h3::text"
    assert selectors["link"] == "h3 a" and "data-src" in selectors["image"]
    assert selectors["published"] == {"css": "time::attr(datetime)", "parse": "date"}
    block = auto.selectors_yaml(selectors)
    reparsed, _ = feeddef.parse_definition(f"source: {http.url}/plain\n{block}")
    again = extraction.default_feed_parser(feeddef.make_feed(reparsed, "suggested"))
    assert len(again) == 5 and again[0]["image"] == http.url + "/i/0.jpg" and again[0]["published"].year == 2024
