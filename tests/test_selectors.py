import pytest
from parsel import Selector

from feedxcavator import selectors as sel
from feedxcavator.feeddef import Feed

PAGE = """
<html><body>
<article class="post"><h2>First &amp; <b>best</b></h2><a href="/p/1">read</a>
  <img data-src="//cdn.test/1.jpg?w=100" src="blank.gif"><time datetime="2024-05-01T10:00:00Z">May 1</time>
  <div class="tags"><a>one</a><a>two</a></div><p class="preview">  Summary one  </p></article>
<article class="post"><h2>Second</h2><a href="p/2">read</a><img src="./img/2.png"></article>
<article class="post"><h2> </h2></article>
</body></html>
"""


def feed(**selectors):
    return Feed({"source": "https://example.com/list/index.html", "selectors": selectors})


def test_basic_parts_and_defaults():
    headlines = sel.apply_selectors(PAGE, feed(item="article.post", title="h2", link="a", image="img",
                                               summary="p.preview"))
    assert len(headlines) == 2  # FD-14: the blank item is dropped
    first, second = headlines
    assert first["title"] == "First &amp; <b>best</b>"  # FD-8: inner HTML
    assert first["link"] == "https://example.com/p/1"  # href by default, made absolute
    assert first["image"] == "https://example.com/list/blank.gif"  # src by default
    assert second["link"] == "https://example.com/list/p/2"
    assert second["image"] == "https://example.com/list/img/2.png"
    assert isinstance(first["html"], Selector)  # FD-15


def test_text_attr_and_legacy_attribute_list():
    headlines = sel.apply_selectors(PAGE, feed(
        item="article.post", title="h2::text", link="a::attr(href)",
        image="img::data-src||src",  # SEL-3
    ))
    assert headlines[0]["title"] == "First & best"
    assert headlines[0]["image"] == "https://cdn.test/1.jpg?w=100"  # SEL-6: the scheme of the source
    assert headlines[1]["image"] == "https://example.com/list/img/2.png"
    mixed = sel.apply_selectors(PAGE, feed(item="article.post", image="img::attr(data-src)||src"))
    assert mixed[0]["image"].startswith("https://cdn.test/")


def test_part_options():
    headlines = sel.apply_selectors(PAGE, feed(
        item="article.post",
        title="h2",
        published={"css": "time::attr(datetime)", "parse": "date"},
        category={"css": ".tags a::text", "many": True},
        summary={"css": ".preview::text", "strip": True, "default": ""},
        image={"css": "img::attr(data-src)||src", "regex": r"^(.*?)\?"},
        views={"css": "h2::text", "regex": r"(\w+)$"},
    ))
    first, second = headlines
    assert first["published"].year == 2024 and first["published"].tzinfo is not None
    assert first["category"] == ["one", "two"]
    assert first["summary"] == "Summary one"
    assert first["image"] == "https://cdn.test/1.jpg"
    assert first["views"] == "best"  # an unknown part name becomes an extra field (SEL-8)
    assert second["summary"] == "" and second["category"] == []


def test_root_xpath_and_alternatives():
    headlines = sel.apply_selectors(PAGE, feed(item="article.post", title="xpath:.//h2", link=":root::class"))
    assert headlines[0]["title"] == "First &amp; <b>best</b>"
    assert sel.q1(PAGE, "xpath://article[2]/h2/text()") == "Second"
    assert [sel.text(n) for n in sel.q(PAGE, "h2, time")][:2] == ["First & best", "May 1"]
    assert sel.q(PAGE, "article:nth-of-type(2) > h2::text") == ["Second"]
    assert sel.q1(PAGE, "a[href^='/p']::attr(href)") == "/p/1"


def test_jmespath_on_json():
    data = {"data": {"items": [{"t": "A", "u": "/a", "tags": ["x", "y"]}, {"t": "B", "u": "https://o.test/b"}]}}
    headlines = sel.apply_selectors(data, Feed({
        "source": "https://api.test/v1/list",
        "selectors": {"item": "jmes:data.items", "title": "jmes:t", "link": "jmes:u",
                      "category": {"jmes": "tags", "many": True}},
    }))
    assert [h["title"] for h in headlines] == ["A", "B"]
    assert headlines[0]["link"] == "https://api.test/a"
    assert headlines[0]["category"] == ["x", "y"]


def test_helpers():
    doc = sel.as_doc(PAGE)
    article = sel.q1(doc, "article")
    assert sel.attr(sel.q1(article, "a"), "href") == "/p/1"
    assert sel.outer_html(sel.q1(article, "h2")) == "<h2>First &amp; <b>best</b></h2>"
    assert sel.inner_html(sel.q1(article, "h2")) == "First &amp; <b>best</b>"
    assert sel.text("<p>a <b>b</b></p>") == "a b"
    one = sel.extract(article, base_url="https://example.com/", title="h2::text", link="a")
    assert one["title"] == "First & best" and one["link"] == "https://example.com/p/1"
    assert sel.enlive_to_css(["div.post", ">", "a"]) == "div.post > a"


@pytest.mark.parametrize("url,expected", [
    ("//host/path", "https://host/path"),
    ("/path", "https://example.com/path"),
    ("./path", "https://example.com/dir/path"),
    ("path", "https://example.com/dir/path"),
    ("http://other/x", "http://other/x"),
    ("", None),
])
def test_fix_relative_url(url, expected):
    assert sel.fix_relative_url(url, "https://example.com/dir/page.html") == expected


def test_invalid_selectors_are_reported():
    for bad in ("div[", "xpath://a[", "jmes:a.[", "[:div :> :a]", "a::attr(x)||"):
        with pytest.raises(sel.SelectorError):
            sel.compile_selector(bad)
    assert sel.validate_selectors({"item": "div", "title": "h2["})[0][0] == "title"
