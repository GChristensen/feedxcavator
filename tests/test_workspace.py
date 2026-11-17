import pytest

from feedxcavator import feeddef, workspace
from feedxcavator.models import FeedIndex, LogEntry

FEED = "title: {title}\nsource: https://e.test/{title}\nselectors: {{item: article}}\n"


def test_load_order_and_shared_namespace(ws):
    registry = ws({
        "lib/b_util.py": "def shout(s):\n    return s.upper()\nORDER = ORDER + ['b'] if 'ORDER' in globals() else ['b']\n",
        "lib/a_first.hy": "(setv ORDER (+ (if (in \"ORDER\" (globals)) ORDER []) [\"a\"]))\n(defn greet [s] (shout s))\n",
        "feeds/one/feed.yaml": FEED.format(title="one"),
        "feeds/one/extract.py": "@extractor('one-x')\ndef one_x(feed):\n    return [{'title': greet('hi')}]\n",
        "handlers/h.py": "@handler('ping')\ndef ping(request):\n    return text_page(shout('pong'))\n",
    }, startup=True)
    assert registry.errors == []
    ns = registry.namespace
    assert ns["ORDER"] == ["a", "b"]  # lib is alphabetical by path
    assert ns["greet"]("x") == "X"  # a Hy function calls a Python one defined later (UC2-1)
    assert registry.find_extractor("one-x").fn(None) == [{"title": "HI"}]
    assert registry.feeds["one"].feed.extractor == "one-x"  # UC2-14
    assert "ping" in registry.handlers

    registry = ws({"lib/_order.yaml": "- b_util.py\n- a_first.hy\n"})
    assert registry.namespace["ORDER"] == ["b", "a"]  # UC2-3: the explicit order


def test_feed_code_failure_disables_only_that_feed(ws):
    registry = ws({
        "lib/util.py": "def helper():\n    return 1\n",
        "feeds/good/feed.yaml": FEED.format(title="good"),
        "feeds/good/x.py": "@extractor('good-x')\ndef good_x(feed):\n    return []\nGOOD = helper()\n",
        "feeds/bad/feed.yaml": FEED.format(title="bad"),
        "feeds/bad/a.py": "LEAK = 1\n@extractor('bad-x')\ndef bad_x(feed):\n    return []\n",
        "feeds/bad/b.hy": "(setv ok 1)\n\n(undefined-thing 2)\n",
    }, startup=True)
    bad, good = registry.feeds["bad"], registry.feeds["good"]
    assert good.status == "ok" and bad.status == "error" and bad.feed is None
    assert "LEAK" not in registry.namespace and registry.namespace["GOOD"] == 1  # UC2-9
    assert registry.find_extractor("bad-x") is None and registry.find_extractor("good-x") is not None
    error = registry.errors_for("feeds/bad/b.hy")[0]
    assert error.line == 3 and "undefined_thing" in error.message  # UC2-11
    assert 'File "feeds/bad/b.hy", line 3' in error.traceback
    assert [f["suffix"] for f in registry.active_feeds()] == ["good"]
    assert LogEntry.objects.filter(level="error", source="workspace").count() == 1
    assert FeedIndex.objects.get(suffix="bad").status == "error"


def test_broken_lib_keeps_previous_state(ws):
    ws({"lib/util.py": "VALUE = 1\n", "feeds/f/feed.yaml": FEED.format(title="f")}, startup=True)
    live = workspace.get_registry()
    attempt = ws({"lib/util.py": "VALUE = 2\nraise RuntimeError('boom')\n",
                  "feeds/g/feed.yaml": FEED.format(title="g")})
    assert attempt.abandoned and workspace.get_registry() is live  # UC2-7
    assert live.namespace["VALUE"] == 1 and "g" not in live.feeds
    assert any(e.path == "lib/util.py" and e.line == 2 for e in live.errors)
    fixed = ws({"lib/util.py": "VALUE = 3\n"})
    assert workspace.get_registry() is fixed and fixed.namespace["VALUE"] == 3 and "g" in fixed.feeds
    assert fixed.errors == []


def test_swap_does_not_touch_running_state(ws):
    old = ws({"lib/util.py": "def version():\n    return 1\n"}, startup=True)
    running = old.namespace["version"]
    new = ws({"lib/util.py": "def version():\n    return 2\n"})
    assert running() == 1 and new.namespace["version"]() == 2  # UC2-5
    assert new.generation == old.generation + 1


def test_identity_and_duplicates(ws):
    original = "# note\ntitle: A\nsource: https://e.test/a\nselectors: {item: p}\n"
    registry = ws({
        "feeds/a/feed.yaml": original,
        "feeds/b/feed.yaml": "id: " + "1" * 32 + "\nsuffix: same\nsource: https://e.test\nselectors: {item: p}\n",
        "feeds/c/feed.yaml": "id: " + "1" * 32 + "\nsource: https://e.test\nselectors: {item: p}\n",
        "feeds/d/feed.yaml": "suffix: Same\nsource: https://e.test\nselectors: {item: p}\n",
        "feeds/новости/feed.yaml": "source: https://e.test\nselectors: {item: p}\n",
        "feeds/новости-ok/feed.yaml": "suffix: news\nsource: https://e.test\nselectors: {item: p}\n",
    }, startup=True)
    assert (ws.root / "feeds/a/feed.yaml").read_text() == original  # the server never rewrites a definition
    a = registry.feeds["a"]
    assert a.id is None and a.key == "a" and a.suffix == "a" and a.feed.key == "a"
    b = registry.feeds["b"]
    assert b.status == "ok" and b.id == "1" * 32 and b.key == "1" * 32 and b.suffix == "same"
    assert "duplicate id" in registry.feeds["c"].error  # the later feed is disabled
    assert "duplicate suffix" in registry.feeds["d"].error  # FD2-3, case-insensitively: stores are files
    assert "cannot be used as the suffix" in registry.feeds["новости"].error  # ASCII only
    assert registry.feeds["новости-ok"].status == "ok" and registry.feeds["новости-ok"].key == "news"
    assert registry.feed_by_key("a") is a.feed and registry.feed_by_id("a") is None
    assert registry.feed_by_id("1" * 32) is b.feed
    assert set(FeedIndex.objects.filter(status="ok").values_list("id", flat=True)) == {"a", "1" * 32, "news"}

    # a feed with an id keeps its identity when its directory is renamed; one without follows the name
    (ws.root / "feeds" / "b").rename(ws.root / "feeds" / "b-moved")
    (ws.root / "feeds" / "a").rename(ws.root / "feeds" / "a-moved")
    moved = ws({})
    assert moved.feeds["b-moved"].key == "1" * 32 and moved.feeds["b-moved"].suffix == "same"
    assert moved.feeds["a-moved"].key == "a-moved"


def test_duplicate_registration_names_both_files(ws):
    registry = ws({
        "feeds/a/feed.yaml": FEED.format(title="a"),
        "feeds/a/x.py": "@extractor('same')\ndef a(feed):\n    return []\n",
        "feeds/b/feed.yaml": FEED.format(title="b"),
        "feeds/b/x.py": "@extractor('same')\ndef b(feed):\n    return []\n",
    }, startup=True)
    assert registry.feeds["a"].status == "ok" and registry.feeds["b"].status == "error"  # UC2-13
    message = registry.feeds["b"].error
    assert "feeds/a/x.py" in message and "feeds/b/x.py" in message


def test_failed_handler_file(ws):
    ws({"handlers/h.py": "@handler('one')\ndef one(request):\n    return 'x'\n"}, startup=True)
    registry = ws({"handlers/h.py": "@handler('one')\ndef one(request):\n    return 'x'\n1/0\n",
                   "handlers/ok.py": "@handler('two')\ndef two(request):\n    return 'y'\n"})
    assert "one" not in registry.handlers and registry.failed_handlers == {"one": "handlers/h.py"}  # UC2-10
    assert "two" in registry.handlers


def test_hy_dsl(ws):
    registry = ws({
        "lib/dsl.hy": """
(defproxy tor :host "127.0.0.1" :port 9050 :scheme "socks5")
(defselectors post :item "div.post" :title "h2" :link "a::attr(href)")
(deftaskfn warm-up [ctx] (:task ctx))
(defhandler ^:auth secret [a b] (text-page (+ a b)))
(defhandler whole request (:params request))
(defextractor my-extractor [feed]
  (-> (parse-html "<div class='post'><h2>T</h2><a href='/x'>l</a></div>")
      (post "https://e.test/")))
(defn probe []
  (setv page (parse-html "<div class='post'><h2>A <i>b</i></h2><a href='/1'>x</a></div>")
        h {"title" "T" "image-type" 1 "image_type" "png"})
  {"all" (len ($ page "div.post"))
   "first" ($1 page "h2::text")
   "threaded" (-> page ($1 "div.post") ($1 "h2") text)
   "enlive" (len ($ page [:div.post :> :a]))
   "kw" [(:title h) (:image-type h) (:missing h)]
   "cond" (cond-> 1 True (+ 1) False (* 100) True (* 3))
   "some" (some-> None (+ 1))
   "assoc" (assoc h "title" "U")
   "update" (update {"n" 1} "n" + 5)
   "get-in" (get-in {"a" {"b" [10 20]}} ["a" "b" 1])
   "assoc-in" (assoc-in {"a" {"b" 1}} ["a" "c"] 2)
   "merge" (merge {"a" 1} {"b" 2} {"a" 3})
   "select" (select-keys {"a" 1 "b" 2} ["b"])
   "distinct" (distinct-by "k" [{"k" 1} {"k" 1} {"k" 2}])
   "keep" (keep (fn [x] (when (> x 1) x)) [1 2 3])
   "mapcat" (mapcat (fn [x] [x x]) [1 2])
   "partition" (partition 2 [1 2 3 4 5])
   "group" (group-by (fn [x] (% x 2)) [1 2 3])
   "freq" (frequencies ["a" "b" "a"])
   "safe" (safe-call (/ 1 0))
   "repeat" (safely-repeat (/ 1 0))
   "original" h})
""",
    }, startup=True)
    assert registry.errors == []
    assert registry.proxies["tor"]["port"] == 9050 and registry.proxies["tor"]["scheme"] == "socks5"
    assert registry.handlers["secret"].meta == {"auth": True} and registry.handlers["whole"].meta == {"auth": False}
    assert registry.find_task_function("warm_up").fn({"task": "t"}) == "t"
    headlines = registry.find_extractor("my-extractor").fn(None)
    assert headlines[0]["title"] == "T" and headlines[0]["link"] == "https://e.test/x"
    result = registry.namespace["probe"]()
    assert result["all"] == 1 and result["first"] == "A b" and result["threaded"] == "A b" and result["enlive"] == 1
    assert result["kw"] == ["T", "png", None]  # HY-3
    assert result["cond"] == 6 and result["some"] is None
    assert result["assoc"]["title"] == "U" and result["original"]["title"] == "T"  # HY-2: no mutation
    assert result["update"] == {"n": 6} and result["get-in"] == 20
    assert result["assoc-in"] == {"a": {"b": 1, "c": 2}} and result["merge"] == {"a": 3, "b": 2}
    assert result["select"] == {"b": 2} and result["distinct"] == [{"k": 1}, {"k": 2}]
    assert result["keep"] == [2, 3] and result["mapcat"] == [1, 1, 2, 2]
    assert result["partition"] == [[1, 2], [3, 4]] and result["group"] == {1: [1, 3], 0: [2]}
    assert result["freq"] == {"a": 2, "b": 1} and result["safe"] is None and result["repeat"] is None


def test_load_js_reads_next_to_the_calling_file(ws):
    registry = ws({
        "feeds/group/lib/shared.hy": '(defn shared-js [] (load-js "common"))\n',
        "feeds/group/lib/common.js": "const main = () => 2;",
        "feeds/group/item/feed.yaml": "title: Item\nsuffix: item\nsource: https://e.test/\n",
        "feeds/group/item/extract.hy": ('(defn own-js [] (load-js "cards.js"))\n'
                                        '(defn raw-js [] (load-js "cards" :wrap False))\n'
                                        '(defn via-lib [] (shared-js))\n'
                                        '(defn escape [] (load-js "../../../../x.js"))\n'
                                        '(defn no-main [] (load-js "plain"))\n'),
        "feeds/group/item/cards.js": "function main() { return 42; } // done",
        "feeds/group/item/plain.js": "42",
    }, startup=True)
    assert registry.errors == []
    ns = registry.namespace
    assert ns["own_js"]() == "async (arg) => {\nfunction main() { return 42; } // done\nreturn await main(arg);\n}"
    assert ns["raw_js"]() == "function main() { return 42; } // done"
    assert "const main = () => 2;" in ns["via_lib"]()  # next to the group lib that calls it, not the feed
    with pytest.raises(ValueError, match="does not define a main function"):
        ns["no_main"]()
    with pytest.raises(ValueError, match="outside the workspace"):
        ns["escape"]()


def test_hy_selector_literal_is_checked_at_load(ws):
    registry = ws({"lib/bad.hy": '(defn f [page]\n  ($ page "div["))\n'}, startup=True)
    assert registry.errors and "div[" in registry.errors[0].message  # SEL-11


def test_tasks_and_schedules_are_resolved(ws):
    registry = ws({
        "lib/fns.py": "@task_function('refresh')\ndef refresh(ctx):\n    return True\n",
        "feeds/news/local/paper/feed.yaml": FEED.format(title="paper") + "tags: [news]\n",
        "feeds/news/world/feed.yaml": FEED.format(title="world") + "tasks: [Extra]\n",
        "feeds/video/x/feed.yaml": FEED.format(title="x") + "enabled: false\ntags: [news]\n",
        "tasks.yaml": "tasks:\n  news: {feeds: {tags: [news]}}\n  local: {feeds: {path: 'news/local/*'}}\n"
                      "  byname: {feeds: {list: [world]}}\n  morning: {pre: refresh, run: [news, extra]}\n"
                      "  nested: {run: [morning, local]}\n  broken: {pre: nope}\n",
        "schedules.yaml": "schedules:\n  - task: morning\n    cron: '0 7 * * *'\n",
    }, startup=True)
    suffixes = lambda name: sorted(f["suffix"] for f in registry.task_feeds(name))  # noqa: E731
    assert suffixes("news") == ["paper"]  # disabled feeds are excluded
    assert suffixes("local") == ["paper"] and suffixes("byname") == ["world"]
    assert suffixes("extra") == ["world"]  # TSK-1: case-insensitive, no declaration needed
    assert suffixes("morning") == ["paper", "world"] and suffixes("nested") == ["paper", "world"]  # TSK2-3
    assert suffixes("nobody") == []  # TSK-2
    assert registry.task_names() == ["broken", "byname", "Extra", "local", "morning", "nested", "news"]
    assert "nope" in registry.task("broken").error
    assert registry.schedules[0].task == "morning"


def test_scratch_is_isolated(ws):
    ws({"lib/util.py": "def shout(s):\n    return s.upper()\n"}, startup=True)
    from feedxcavator import applog

    applog.install_stdout()
    result = workspace.run_scratch('(print (shout "hi"))\n(setv shout 5)\n(defextractor leak [feed] [])\n', "scratch.hy")
    assert result["output"] == "HI\n" and result["error"] is None  # UC2-20
    assert callable(workspace.get_registry().namespace["shout"])
    assert workspace.get_registry().find_extractor("leak") is None
    failed = workspace.run_scratch("print('before')\n1/0\n", "scratch.py")
    assert failed["output"].startswith("before\n") and "ZeroDivisionError" in failed["output"]
    assert failed["error"]["line"] == 2


def test_signature_skips_unchanged_reloads(ws):
    first = ws({"feeds/a/feed.yaml": FEED.format(title="a")}, startup=True)
    assert workspace.load(force=False) is workspace.get_registry()
    assert workspace.get_registry().generation == first.generation  # nothing changed: no reload
    (ws.root / "feeds/a/feed.yaml").write_text(
        (ws.root / "feeds/a/feed.yaml").read_text() + "tags: [x]\n")
    assert workspace.load(force=False).generation == first.generation + 1


def test_group_libraries(ws):
    registry = ws({
        "lib/util.py": "def shout(s):\n    return s.upper()\n",
        "lib/base.yaml": "selectors: {item: div}\nlimit: 5\n",
        "feeds/news/lib/common.py": "def news_title(s):\n    return 'news: ' + shout(s)\n"
                                    "@extractor('news-x')\ndef news_x(feed):\n    return [{'title': news_title('a')}]\n",
        "feeds/news/lib/base.yaml": "extends: base\nselectors: {item: article}\n",
        "feeds/news/lib/local/lib.hy": "(defn local-title [s] (+ \"local \" (news-title s)))\n",
        "feeds/news/local/lib/more.py": "def deeper(s):\n    return local_title(s) + '!'\n",
        "feeds/news/local/city/feed.yaml": "title: city\nsource: https://e.test/c\nextends: base\n",
        "feeds/news/local/city/x.py": "@extractor('city-x')\ndef city_x(feed):\n    return [{'title': deeper('c')}]\n"
                                      "CITY = 1\n",
        "feeds/news/paper/feed.yaml": "title: paper\nsource: https://e.test/p\nextends: base\nextractor: news-x\n",
        "feeds/other/feed.yaml": "title: other\nsource: https://e.test/o\nextends: base\n",
        "feeds/other/x.py": "HAS_NEWS = 'news_title' in globals()\n",
    }, startup=True)
    assert registry.errors == []
    assert sorted(registry.feeds) == ["news/local/city", "news/paper", "other"]  # lib/ is not a feed group
    city = registry.feeds["news/local/city"].feed
    assert registry.find_extractor("city-x").fn(city) == [{"title": "local news: C!"}]
    assert registry.find_extractor("news-x") is not None  # registrations are global
    # group names stay in the group, the feed's own names are shared as before (UC2-9)
    ns = registry.namespace
    assert "news_title" not in ns and "local_title" not in ns and "deeper" not in ns and ns["CITY"] == 1
    assert ns["HAS_NEWS"] is False
    # templates resolve from the nearest group lib/, then lib/
    assert city["selectors"]["item"] == "article" and city["limit"] == 5
    assert registry.feeds["other"].feed["selectors"]["item"] == "div"


def test_broken_group_library_disables_its_group(ws):
    registry = ws({
        "feeds/news/lib/bad.py": "X = 1\nraise RuntimeError('boom')\n",
        "feeds/news/a/feed.yaml": FEED.format(title="a"),
        "feeds/b/feed.yaml": FEED.format(title="b"),
    }, startup=True)
    assert not registry.abandoned
    assert registry.feeds["news/a"].status == "error" and "feeds/news/lib/bad.py" in registry.feeds["news/a"].error
    assert registry.feeds["news/a"].suffix == "a"  # the identity is kept, so stored output is still served
    assert registry.feeds["b"].status == "ok"
    assert [e.line for e in registry.errors_for("feeds/news/lib/bad.py")] == [2]


def test_feed_named_lib_is_still_a_feed(ws):
    registry = ws({"feeds/lib/feed.yaml": FEED.format(title="lib")})
    assert "lib" in registry.feeds and registry.feeds["lib"].status == "ok"


def test_group_template_cycle(ws):
    registry = ws({
        "lib/base.yaml": "extends: base\n",
        "feeds/g/lib/base.yaml": "extends: base\n",
        "feeds/g/f/feed.yaml": "title: f\nsource: https://e.test/f\nextends: base\n",
    }, startup=True)
    assert "cyclic 'extends': base -> base -> base" in registry.feeds["g/f"].error
