import base64
import io
import json
import zipfile
from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client

from conftest import html
from feedxcavator import conf, filters, storage, workspace
from feedxcavator.models import ApiToken, KVObject, QueueItem, Subscription, WordFilterEntry

API = "/front/api"


@pytest.fixture
def client():
    return Client()


def token(client):
    return client.post(f"{API}/token").json()["token"]


def put(client, path, content, **extra):
    return client.put(f"{API}/file", data=json.dumps({"path": path, "content": content, **extra}),
                      content_type="application/json")


def post(client, url, body=None):
    return client.post(f"{API}{url}", data=json.dumps(body or {}), content_type="application/json")


def body_of(response) -> bytes:
    if hasattr(response, "streaming_content"):
        import asyncio

        async def collect():
            return b"".join([chunk async for chunk in response.streaming_content])

        return asyncio.run(collect()) if hasattr(response.streaming_content, "__aiter__") else b"".join(
            response.streaming_content)
    return response.content


def test_feed_routes(http, ws, client):
    http.routes["/live"] = html('<article><h2>Live</h2><a href="/1">l</a></article>')
    http.routes["/none"] = html("<p>nothing</p>")
    registry = ws({
        "feeds/live/feed.yaml": f"id: {'c' * 32}\nsource: {http.url}/live\n"
                                "selectors: {item: article, title: h2, link: a}\n",
        "feeds/empty/feed.yaml": f"source: {http.url}/none\nselectors: {{item: article, title: h2}}\n",
        "feeds/task/feed.yaml": f"source: {http.url}/live\nselectors: {{item: article, title: h2}}\ntasks: [t]\n",
        "feeds/off/feed.yaml": f"source: {http.url}/live\nselectors: {{item: article}}\nenabled: false\n",
        "feeds/broken/feed.yaml": f"source: {http.url}/live\nselectors: {{item: article}}\n",
        "feeds/broken/x.py": "1/0\n",
    }, startup=True)
    response = client.get("/feed/live")
    assert response.status_code == 200 and response["Content-Type"] == "application/rss+xml"
    assert response["Cache-Control"] == "no-cache" and b"<title>Live</title>" in response.content
    assert client.get(f"/feed/uuid:{'c' * 32}").status_code == 200  # only for feeds that set an id
    assert client.get("/feed/uuid:task").status_code == 404
    assert client.get("/feed/empty").status_code == 204
    assert client.get("/feed/unknown").status_code == 404
    assert client.get("/feed/off").status_code == 404  # a disabled feed

    requests_before = len(http.requests)
    assert client.get("/feed/task").status_code == 204  # nothing stored yet
    storage.store_feed_output(registry.feeds["task"].key, "<rss>stored</rss>", "application/rss+xml", 1)
    stored = client.get("/feed/task")
    assert stored.content == b"<rss>stored</rss>" and len(http.requests) == requests_before  # a feed with tasks is never extracted live

    assert client.get("/feed/broken").status_code == 503  # a failed feed
    storage.store_feed_output(registry.feeds["broken"].key, "last good", "application/rss+xml", 1)
    assert client.get("/feed/broken").content == b"last good"


def test_images(client):
    data = bytes(range(256)) * 4
    assert client.post("/api/images/save", data=data, content_type="image/png").status_code == 403  # needs the API token
    secret = token(client)
    saved = client.post("/api/images/save", data=data, content_type="image/png", headers={"x-feedxcavator-auth": secret})
    assert saved.status_code == 200
    url = f"/image/{saved.content.decode()}"

    whole = client.get(url)
    assert whole.status_code == 200 and body_of(whole) == data
    assert whole["Content-Type"] == "image/png" and whole["Content-Length"] == "1024"
    assert whole["Accept-Ranges"] == "bytes" and whole["Cache-Control"] == "public, max-age=31536000, immutable"
    etag, modified = whole["ETag"], whole["Last-Modified"]

    part = client.get(url, headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and body_of(part) == data[10:20]
    assert part["Content-Range"] == "bytes 10-19/1024" and part["Content-Length"] == "10"
    assert body_of(client.get(url, headers={"Range": "bytes=1000-"})) == data[1000:]
    suffix = client.get(url, headers={"Range": "bytes=-5"})
    assert body_of(suffix) == data[-5:] and suffix["Content-Range"] == "bytes 1019-1023/1024"
    bad = client.get(url, headers={"Range": "bytes=2000-"})
    assert bad.status_code == 416 and bad["Content-Range"] == "bytes */1024"
    assert client.get(url, headers={"Range": "bytes=0-1,5-6"}).status_code == 200  # multi-range: the whole file
    assert client.get(url, headers={"Range": "bytes=0-9", "If-Range": etag}).status_code == 206
    assert client.get(url, headers={"Range": "bytes=0-9", "If-Range": modified}).status_code == 206
    assert client.get(url, headers={"Range": "bytes=0-9", "If-Range": '"other"'}).status_code == 200
    assert client.get(url + ".jpg").status_code == 200  # 3.0.0 names carried an extension
    assert client.get("/image/missing").status_code == 404

    conf.config["x-accel"] = "/protected-blobs/"
    try:
        accel = client.get(url)
        assert accel["X-Accel-Redirect"].startswith("/protected-blobs/") and accel.content == b""  # served by nginx
    finally:
        conf.config["x-accel"] = None


def test_handlers(ws, client):
    ws({"handlers/h.py": """
@handler("sum")
def total(a, b):
    return text_page(str(int(a) + int(b)))

@handler("whole")
def whole(request):
    return json_page({"method": request.method, "q": request.params.get("q"), "source": __import__("feedxcavator").context.logging_source.get()})

@handler("private", auth=True)
def private(request):
    return redirect_to("https://e.test/there")
""", "handlers/hy_one.hy": '(defhandler ^:auth hy-secret [name] (html-page (+ "hi " name)))\n'}, startup=True)
    assert client.get("/handler/sum?a=2&b=3").content == b"5"  # parameters by name
    assert client.post("/handler/sum", {"a": "4", "b": "5"}).content == b"9"  # every method, form parameters
    assert client.put("/handler/whole?q=x").json() == {"method": "PUT", "q": "x", "source": "whole"}  # the logging source is the handler name
    assert client.get("/handler/nope").status_code == 404
    assert client.get("/handler/private").status_code == 403  # needs the API token
    secret = token(client)
    allowed = client.get("/handler/private", headers={"x-feedxcavator-auth": secret})
    assert allowed.status_code == 302 and allowed["Location"] == "https://e.test/there"  # redirect_to answers 302
    assert client.get("/handler/hy-secret?name=you", headers={"x-feedxcavator-auth": secret}).content == b"hi you"
    assert client.get("/handler/private", headers={"x-feedxcavator-auth": "wrong"}).status_code == 403
    assert ApiToken.objects.get().token_hash != secret  # only the hash is stored

    ws({"handlers/h.py": "@handler('sum')\ndef total(a, b):\n    return 'x'\nraise RuntimeError('broken')\n"})
    assert client.get("/handler/sum?a=1&b=2").status_code == 503  # its file failed
    assert client.get("/handler/hy-secret?name=x", headers={"x-feedxcavator-auth": secret}).status_code == 200


def test_redirect_and_forward(http, ws, client):
    ws({}, startup=True)
    target = "https://e.test/a/b?x=1&y=2"
    response = client.get(f"/redirect/abc123/{quote(target, safe='')}")
    assert response.status_code == 302 and response["Location"] == target  # encoded slashes
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert client.get(f"/redirect-b64/abc/{encoded}")["Location"] == target
    assert client.get("/redirect/abc/javascript%3Aalert(1)").status_code == 404
    assert client.get("/redirect/abc/not-a-url").status_code == 404
    custom = "ext+jb://main#0f9e"  # custom schemes pass through, as with Ring
    assert client.get(f"/redirect/_/{quote(custom, safe='')}")["Location"] == custom
    custom_b64 = base64.urlsafe_b64encode(custom.encode()).decode().rstrip("=")
    assert client.get(f"/redirect-b64/_/{custom_b64}")["Location"] == custom

    http.routes["/media"] = lambda h: (200, {"Content-Type": "video/mp4", "X-Seen-Referer": h.headers.get("Referer", "")},
                                       b"media-bytes")
    upstream = f"{http.url}/media"
    forwarded = client.get(f"/forward/r4nd/?url={quote(upstream, safe='')}&referer={quote('https://ref.test/')}")
    assert forwarded.status_code == 200 and forwarded.content == b"media-bytes"
    assert forwarded["Content-Type"] == "video/mp4" and forwarded["X-Seen-Referer"] == "https://ref.test/"
    data = base64.urlsafe_b64encode(f"{upstream};;https://b64.test/".encode()).decode().rstrip("=")
    assert client.get(f"/forward-b64/{data}")["X-Seen-Referer"] == "https://b64.test/"

    from feedxcavator import userlib

    assert userlib.redirect_url_b64(target).startswith("http://feeds.test/redirect-b64/")
    assert userlib.forward_url(upstream, None).endswith("&referer=")  # None is no referer, not "None"
    assert userlib.url_safe_base64dec(userlib.forward_url_b64(upstream, None).rsplit("/", 1)[1]).endswith(";;")
    assert f"/forward-b64/{data}" in userlib.forward_url_b64(upstream, "https://b64.test/")


def test_backend_websub_and_wordfilter_routes(ws, client):
    ws({"feeds/f/feed.yaml": "source: https://e.test\nselectors: {item: p}\n"}, startup=True)
    assert client.get("/backend/run/news").content == b"news enqueued"
    assert client.get("/backend/service-task").content == b"OK"
    assert client.get("/backend/reschedule").content == b"OK"
    assert list(QueueItem.objects.values_list("kind", flat=True)) == ["task", "service-task", "reschedule"]

    assert client.get("/websub").status_code == 405
    assert client.post("/websub", {"hub.mode": "subscribe", "hub.topic": "http://feeds.test/feed/zzz",
                                   "hub.callback": "http://127.0.0.1:9/cb"}).status_code == 404
    assert client.post("/websub", {"hub.mode": "subscribe", "hub.topic": "http://feeds.test/feed/f",
                                   "hub.callback": "http://127.0.0.1:9/cb"}).status_code == 202
    assert Subscription.objects.count() == 1
    assert client.post("/websub", {"hub.mode": "unsubscribe", "hub.topic": "http://feeds.test/feed/f",
                                   "hub.callback": "http://127.0.0.1:9/cb"}).status_code == 202
    assert Subscription.objects.count() == 0

    def wf(action, body=None, auth=True):
        headers = {"x-feedxcavator-auth": secret} if auth else {}
        return client.post(f"/api/wordfilter/{action}", data=json.dumps(body or {}),
                           content_type="application/json", headers=headers)

    secret = token(client)
    assert wf("list", auth=False).status_code == 403
    assert wf("list").json() == []
    assert wf("add-regex", {}).status_code == 404  # an unknown filter
    assert wf("add-regex", {"regex": "foo.*bar"}).status_code == 204
    assert wf("add-regex", {"word-filter": "other", "regex": "x+"}).status_code == 204
    assert wf("list").json() == ["default", "other"]
    assert wf("list-words").json() == ["foo.*bar"]  # regexes as their source text
    assert wf("list-words", {"word-filter": "missing"}).status_code == 404
    assert wf("remove-regex", {"regex": "foo.*bar"}).status_code == 204
    assert WordFilterEntry.objects.filter(filter__name="default").count() == 0
    assert filters.matches_filter("Some FOOD", ["foo"]) and not filters.matches_filter("bar", ["foo"])  # case-insensitive containment


def test_authentication(ws, client):
    ws({"feeds/f/feed.yaml": "source: https://e.test\nselectors: {item: p}\ntasks: [t]\n"}, startup=True)
    conf.config["authentication"] = True
    try:
        call_command("setpassword", user="user", password="correct horse", verbosity=0)
        assert get_user_model().objects.get().check_password("correct horse")
        for path in ("/console", "/backend/run/t", "/backend/reschedule"):
            response = client.get(path)
            assert response.status_code == 302 and response["Location"] == f"/login?next={quote(path)}"
        assert client.get(f"{API}/tree").status_code == 401
        for path in ("/feed/f", "/image/x", "/handler/x", "/login"):
            assert client.get(path).status_code in (200, 204, 404)
        assert client.get("/").status_code == 302 and client.get("/")["Location"] == "/console"

        assert client.post("/login", {"username": "user", "password": "wrong", "next": "/console"}).status_code == 401
        ok = client.post("/login", {"username": "user", "password": "correct horse", "next": "/console#log"})
        assert ok.status_code == 302 and ok["Location"] == "/console#log"
        cookie = ok.cookies["feedxcavator_sessionid"]
        assert cookie["httponly"] and cookie["samesite"] == "Strict"
        assert client.get(f"{API}/tree").status_code == 200
        assert client.get("/backend/run/t").content == b"t enqueued"
        assert client.post("/login", {"username": "user", "password": "correct horse",
                                      "next": "//evil.test"})["Location"] == "/"
        assert client.get("/logout")["Location"] == "/login"
        assert client.get(f"{API}/tree").status_code == 401
    finally:
        conf.config["authentication"] = False


def test_tree_lists_group_lib_first(ws):
    ws({"lib/util.py": "", "feeds/a/a.py": "", "feeds/news/aaa/feed.yaml": "title: a\n",
        "feeds/news/lib/util.py": "", "feeds/news/zzz.py": ""}, startup=True)
    tree = Client().get(f"{API}/tree").json()["tree"]
    assert [n["name"] for n in tree if n["name"] in ("feeds", "lib")] == ["feeds", "lib"]  # top level unchanged
    news = next(n for n in next(n for n in tree if n["name"] == "feeds")["children"] if n["name"] == "news")
    assert [c["name"] for c in news["children"]] == ["lib", "aaa", "zzz.py"]


def test_csrf_protects_console_api(ws):
    ws({}, startup=True)
    strict = Client(enforce_csrf_checks=True)
    assert strict.get(f"{API}/tree").status_code == 200
    assert strict.post(f"{API}/reload").status_code == 403  # no CSRF token
    strict.get("/console")  # sets the CSRF cookie
    csrf = strict.cookies["feedxcavator_csrftoken"].value
    assert strict.post(f"{API}/reload", headers={"X-CSRFToken": csrf}).status_code == 200
    assert strict.post("/websub", {"hub.mode": "nonsense"}).status_code == 400  # public routes are exempt
    assert strict.get(f"{API}/log").status_code == 200 and strict.delete(f"{API}/log").status_code == 403  # deleting the log needs the CSRF token


def test_console_api_files_and_feeds(http, ws, client):
    ws({"lib/util.py": "def shout(s):\n    return s.upper()\n"}, startup=True)
    http.routes["/list"] = html("".join(
        f'<div class="card"><h3><a href="/n/{i}">Headline number {i} here</a></h3><p>Some text for item {i}, long '
        f'enough to count.</p></div>' for i in range(4)))

    created = post(client, "/feed/new", {"dir": "news/local"}).json()
    assert created["path"] == "feeds/news/local/untitled-feed/feed.yaml" and created["load"]["ok"]
    text = client.get(f"{API}/file", {"path": created["path"]}).json()
    assert text["lang"] == "yaml" and "title: '*Untitled feed'" in text["content"]
    new = workspace.get_registry().feeds["news/local/untitled-feed"]
    assert new.id is None and new.key == "untitled-feed"
    assert "\nid:" not in text["content"] and not text["content"].startswith("id:")  # no id is written
    assert post(client, "/feed/new", {"dir": "news/local"}).json()["dir"].endswith("untitled-feed-2")

    yaml_text = f"title: Cards\nsuffix: cards\nsource: {http.url}/list\n"
    feed_id = "cards"  # the suffix is the identity
    suggestion = post(client, "/feed/suggest", {"path": created["path"], "yaml": yaml_text}).json()
    assert suggestion["selectors"]["item"].endswith("div.card") and suggestion["yaml"].startswith("selectors:\n")
    assert (ws.root / created["path"]).read_text().count("Cards") == 0  # nothing is saved

    full = yaml_text + suggestion["yaml"]
    tested = post(client, "/feed/test", {"path": created["path"], "yaml": full}).json()
    assert tested["count"] == 4 and tested["content_type"] == "application/rss+xml"
    assert "\n    <item>\n      <title>Headline number 0 here</title>" in tested["output"]  # pretty-printed

    invalid = put(client, created["path"], full + "output: edn\n")
    assert invalid.status_code == 400 and invalid.json()["diagnostics"][0]["line"] == full.count("\n") + 1  # the line of the error
    assert "Cards" not in (ws.root / created["path"]).read_text()

    saved = put(client, created["path"], full, base_mtime=text["mtime"]).json()
    assert saved["load"]["ok"] and saved["feed"]["title"] == "Cards"  # the result of the reload
    assert (ws.root / created["path"]).read_text() == full  # stored verbatim
    stale = put(client, created["path"], full + "#x\n", base_mtime=text["mtime"])
    assert stale.status_code == 409  # changed on disk since it was opened
    assert put(client, created["path"], full + "#x\n", base_mtime=text["mtime"], force=True).status_code == 200
    assert client.get(f"{API}/feed/url", {"key": feed_id}).json() == {"url": "http://feeds.test/feed/cards"}

    code = put(client, "feeds/news/local/untitled-feed/extract.py",
               "@extractor('cards-x')\ndef cards(feed):\n    return [{'title': shout('x'), 'link': feed.source}]\n")
    assert code.json()["load"]["ok"]
    assert post(client, "/feed/test", {"path": created["path"], "yaml": yaml_text}).json()["count"] == 1  # the only extractor in the directory is used
    broken = put(client, "feeds/news/local/untitled-feed/extract.py", "def oops(:\n").json()
    assert not broken["load"]["ok"] and broken["file_errors"][0]["line"] == 1
    assert broken["feed"]["status"] == "error"

    tree = client.get(f"{API}/tree").json()
    feeds_dir = next(n for n in tree["tree"] if n["name"] == "feeds")
    local = feeds_dir["children"][0]["children"][0]
    node = next(n for n in local["children"] if n["name"] == "untitled-feed")
    assert node["type"] == "feed" and node["feed"]["title"] == "Cards" and node["feed"]["status"] == "error"
    assert [c["name"] for c in node["children"]] == ["extract.py"]  # code files are the children
    assert "feeds/news/local/untitled-feed/extract.py" in tree["errors"]

    moved = post(client, "/file/move", {"src": "feeds/news/local/untitled-feed", "dst": "feeds/cards"}).json()
    assert moved["path"] == "feeds/cards"
    assert workspace.get_registry().feeds["cards"].key == feed_id  # an explicit suffix survives a move
    assert post(client, "/file/create", {"path": "lib/forums", "kind": "dir"}).status_code == 200
    assert post(client, "/file/create", {"path": "lib/forums/x.hy", "content": "(setv X 1)\n"}).json()["load"]
    assert client.get(f"{API}/file", {"path": "../config.yml"}).status_code == 400
    assert put(client, "../evil.py", "x").status_code == 400
    storage.store_feed_output(feed_id, "x", "text/plain")
    Subscription.objects.create(feed_id=feed_id, topic="t", callback="c", created_at="2024-01-01T00:00:00Z")
    assert client.delete(f"{API}/file?path=feeds/cards").status_code == 200
    assert "cards" not in workspace.get_registry().feeds and Subscription.objects.count() == 0  # the subscription goes with the feed
    assert storage.fetch_feed_output(feed_id) is None


def test_console_api_tasks_schedules_and_misc(ws, client):
    ws({
        "feeds/a/feed.yaml": "title: Alpha\nsource: https://e.test\nselectors: {item: p}\ntasks: [news]\n",
        "tasks.yaml": "tasks:\n  morning: {run: [news]}\n",
        "schedules.yaml": "# my schedules\nschedules:\n  - task: news   # daily\n    cron: '0 12 * * *'\n",
        "scratch.py": "print('x')\n",
    }, startup=True)
    tasks = {t["name"]: t for t in client.get(f"{API}/tasks").json()}
    assert tasks["news"]["declared"] is False and tasks["morning"]["feeds"] == 1
    assert client.get(f"{API}/tasks/feeds", {"task": "morning"}).json()[0]["title"] == "Alpha"
    assert post(client, "/tasks/run", {"task": "morning"}).json() == {"enqueued": "morning"}
    assert workspace.get_registry().feeds["a"].key == "a"
    assert post(client, "/feed/run-tasks", {"key": "a"}).json() == {"enqueued": ["news"]}
    assert QueueItem.objects.count() == 2

    rows = client.get(f"{API}/schedules").json()["schedules"]
    assert rows[0]["task"] == "news" and len(rows[0]["next"]) == 1 and "12:00" in rows[0]["description"]
    assert post(client, "/schedules/save", {"index": 0, "cron": "30 8 * * mon", "enabled": False}).status_code == 200
    assert post(client, "/schedules/save", {"task": "morning", "cron": "0 6 15 * *",
                                            "timezone": "Europe/Kyiv"}).status_code == 200
    text = (ws.root / "schedules.yaml").read_text()
    assert "# my schedules" in text and "# daily" in text and "30 8 * * mon" in text  # comments are preserved
    rows = client.get(f"{API}/schedules").json()["schedules"]
    assert [(r["task"], r["enabled"], r["timezone"]) for r in rows] == [("news", False, None),
                                                                        ("morning", True, "Europe/Kyiv")]
    assert rows[0]["next"] == []
    assert post(client, "/schedules/save", {"index": 1, "cron": "bogus"}).status_code == 400
    assert post(client, "/schedules/delete", {"index": 0}).status_code == 200
    assert [s.task for s in workspace.get_registry().schedules] == ["morning"]

    assert post(client, "/scratch/run", {"path": "scratch.py", "content": "print(6 * 7)"}).json()["output"] == "42\n"
    assert client.get(f"{API}/file/validate").status_code == 405
    diagnostics = post(client, "/file/validate", {"path": "tasks.yaml", "content": "tasks:\n  x: {bogus: 1}\n"}).json()
    assert diagnostics["diagnostics"][0]["line"] == 2
    schemas = client.get(f"{API}/schemas").json()
    assert "selectors" in schemas["feed"]["properties"] and "tasks" in schemas["tasks"]["properties"]
    reference = client.get(f"{API}/reference").json()["sections"]
    fetching = next(s for s in reference if s["category"] == "Fetching")
    entry = next(e for e in fetching["entries"] if e["name"] == "fetch_url")
    assert entry["hy"] == "fetch-url" and "Never raises" in entry["doc"]  # from the docstring
    browser_entries = next(s for s in reference if s["category"] == "Browser")["entries"]
    assert any(e["name"].startswith("page.wait_for(") for e in browser_entries)
    assert [e["name"] for e in browser_entries if not e["doc"]] == []  # every public browser method is documented
    manual = client.get(f"{API}/manual").json()
    assert '<h2 id="selectors">Selectors</h2>' in manual["html"] and "<table>" in manual["html"]
    assert any(t["id"] == "selectors" for t in manual["toc"][0]["children"])

    from feedxcavator import applog

    for n in range(5):
        applog.write("warning" if n % 2 else "info", f"entry {n}")
    newest = client.get(f"{API}/log", {"n": 3}).json()
    assert [e["message"] for e in newest] == ["entry 4", "entry 3", "entry 2"]  # newest first, exactly n
    older = client.get(f"{API}/log", {"n": 2, "from": newest[-1]["number"] - 1}).json()
    assert [e["message"] for e in older] == ["entry 1", "entry 0"]
    assert client.delete(f"{API}/log").status_code == 200 and client.get(f"{API}/log").json() == []

    settings = client.get(f"{API}/settings").json()
    assert settings["restartable"] is False and settings["token_created_at"] is None
    client.put(f"{API}/settings", data=json.dumps({"subscription_url": "https://reader.test/?url="}),
               content_type="application/json")
    assert client.get(f"{API}/settings").json()["subscription_url"] == "https://reader.test/?url="
    assert post(client, "/restart").status_code == 409  # not supervised
    assert client.get(f"{API}/runs").json() == []


def test_clear_runs_keeps_runs_in_progress(ws, client):
    from django.utils import timezone

    from feedxcavator.models import FeedRun, TaskRun

    ws({}, startup=True)
    now = timezone.now()
    done = TaskRun.objects.create(task="a", trigger="manual", started_at=now, finished_at=now, status="ok")
    TaskRun.objects.create(task="sub", trigger="subtask", parent=done, started_at=now, status="ok")
    FeedRun.objects.create(task_run=done, feed_id="f", started_at=now, status="ok")
    running = TaskRun.objects.create(task="b", trigger="manual", started_at=now, status="running")

    response = client.delete(f"{API}/runs")
    assert response.status_code == 200 and response.json() == {"deleted": 1}
    assert [r["id"] for r in client.get(f"{API}/runs").json()] == [running.id]
    assert TaskRun.objects.count() == 1 and FeedRun.objects.count() == 0


def test_backup_and_restore(ws, client):
    ws({"feeds/a/feed.yaml": "title: A\nsource: https://e.test\nselectors: {item: p}\n",
        "lib/util.py": "X = 1\n"}, startup=True)
    filters.add_filter_word("spam")
    filters.add_filter_regex("ads?")
    storage.store_object("cookie", {"session": "abc"})
    backup = client.get(f"{API}/backup?workspace=true")
    assert backup["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(backup.content))
    assert {"wordfilters.json", "kvobjects.json", "subscriptions.json", "settings.json", "schedulestate.json",
            "workspace.tar.gz"} <= set(archive.namelist())

    WordFilterEntry.objects.all().delete()
    KVObject.objects.all().delete()
    upload = io.BytesIO(backup.content)
    upload.name = "backup.zip"
    restored = client.post(f"{API}/restore?unpack_workspace=true", {"file": upload}).json()
    assert restored["records"]["wordfilters"] == 1 and restored["workspace_unpacked"] is False
    assert "not empty" in restored["workspace_error"]
    assert sorted(filters.list_word_filter()) == ["ads?", "spam"] and storage.fetch_object("cookie") == {"session": "abc"}

    import shutil

    shutil.rmtree(ws.root / "feeds")
    shutil.rmtree(ws.root / "lib")
    upload = io.BytesIO(backup.content)
    upload.name = "backup.zip"
    restored = client.post(f"{API}/restore?unpack_workspace=true", {"file": upload}).json()
    assert restored["workspace_unpacked"] and (ws.root / "lib/util.py").read_text() == "X = 1\n"
    assert "a" in workspace.get_registry().feeds
    assert client.post(f"{API}/purge-images").json() == {"deleted": 0}


def test_import_3_0_backup(tmp_path, ws):
    ws({}, startup=True)
    edn = tmp_path / "backup.edn"
    edn.write_text('''{:feed-definitions
 ({:uuid "0123456789abcdef0123456789abcdef",
   :yaml "title: Old feed # c\\nsuffix: old-feed\\nsource: https://e.test\\ngroup: news/local\\ntask: news\\nrealtime: true\\npartition: 100\\ncharset: windows-1251\\nproxy: default\\ntimeout: 0\\nselectors:\\n  item: div.headline\\n  title: \\"[:h3 :> :a]\\"\\npages:\\n  path: '/?page=%n'\\n  end: 2\\nfilter:\\n  history: true\\n"}
  {:uuid "11111111111111111111111111111111", :yaml "title: Edn\\nsuffix: edn-feed\\nsource: https://e.test\\noutput: edn\\nextractor: custom\\n"}),
 :feeds (),
 :code ({:type "tasks", :code "(deftask* morning-feeds [news articles] :on-completion [aggregate remind-me])\\n(schedule news 12 00)\\n(schedule-weekly digest 8 30 1) ; mondays\\n(schedule-monthly report 6 0 15)\\n", :timestamp 1}
        {:type "extractors", :code "(defextractor custom [feed] [])"}),
 :word-filters ({:id "default", :words ("spam" "(?iu)ads?")}),
 :objects ({:uuid "cookie", :object {:session "abc", :n 5}}),
 :subscriptions ({:uuid "0123456789abcdef0123456789abcdef", :name "Old feed", :topic "http://h/feed/old-feed", :callback "http://reader/cb", :secret "s", :timestamp 1}),
 :auth-tokens ({:kind "main", :token "abc"}),
 :settings ({:kind "main", :enable-profiling true, :subscription-url "https://r.test/?u=", :user-email nil})}''',
                   encoding="utf-8")
    out = io.StringIO()
    call_command("importbackup", str(edn), stdout=out)
    report = out.getvalue()
    converted = (ws.root / "feeds/news/local/old-feed/feed.yaml").read_text()
    data = __import__("yaml").safe_load(converted)
    assert "id" not in data and data["suffix"] == "old-feed" and "# c" in converted
    assert data["tasks"] == ["news"] and data["websub"] == {"enabled": True, "partition": 100}
    assert data["fetch"] == {"charset": "windows-1251", "proxy": "default"} and data["pages"]["path"] == "/?page={n}"
    assert not {"task", "realtime", "partition", "charset", "proxy", "timeout", "group"} & set(data)
    assert "native Enlive selector in 'title'" in report and "output: edn is no longer supported" in report
    assert (ws.root / "legacy/extractors.clj").read_text() == "(defextractor custom [feed] [])"
    schedules = __import__("yaml").safe_load((ws.root / "schedules.yaml").read_text())["schedules"]
    assert schedules == [{"task": "news", "cron": "0 12 * * *"}, {"task": "digest", "cron": "30 8 * * mon"},
                         {"task": "report", "cron": "0 6 15 * *"}]
    tasks = __import__("yaml").safe_load((ws.root / "tasks.yaml").read_text())["tasks"]
    assert tasks == {"morning-feeds": {"run": ["news", "articles"], "then": ["aggregate", "remind-me"]}}
    assert sorted(WordFilterEntry.objects.values_list("kind", "value")) == [("regex", "ads?"), ("word", "spam")]
    assert storage.fetch_object("cookie") == {"session": "abc", "n": 5}
    assert Subscription.objects.get().feed_id == "old-feed"  # kept under the suffix

    registry = workspace.load(force=True)
    assert registry.feeds["edn-feed"].status == "error"  # needs rewriting: reported, not silently accepted
    assert registry.feeds["edn-feed"].id is None and registry.feeds["edn-feed"].key == "edn-feed"
    assert not any(e.path.startswith("legacy/") for e in registry.errors)  # legacy code is not loaded
    assert [s.task for s in registry.schedules] == ["news", "digest", "report"]


def test_import_keeps_ids_on_request(tmp_path, ws):
    ws({}, startup=True)
    edn = tmp_path / "backup.edn"
    edn.write_text('''{:feed-definitions
 ({:uuid "11111111111111111111111111111111", :yaml "title: One\\nsuffix: one\\nsource: https://e.test\\nselectors: {item: p}\\n"}
  {:uuid "0123456789abcdef0123456789abcdef", :yaml "title: Кириллица\\nsuffix: новости\\nsource: https://e.test\\nselectors: {item: p}\\n"}),
 :subscriptions ({:uuid "11111111111111111111111111111111", :topic "http://h/feed/one", :callback "http://r/cb", :secret nil, :timestamp 1})}''',
                   encoding="utf-8")
    out = io.StringIO()
    call_command("importbackup", str(edn), keep_ids=True, stdout=out)
    registry = workspace.load(force=True)
    one = registry.feeds["one"]
    assert one.status == "ok" and one.id == "1" * 32 and one.key == "1" * 32  # an all-digit id survives YAML
    assert Subscription.objects.get().feed_id == "1" * 32
    other = next(i for i in registry.feeds.values() if i is not one)
    assert other.status == "ok" and other.suffix.startswith("feed-") and "is not ASCII" in out.getvalue()


def test_rename_suffix(http, ws, client):
    from feedxcavator import filters
    from feedxcavator.models import FeedOutput, FeedRun, HistoryKey, TaskRun

    definition = "title: Plain  # t\nsource: https://e.test\nselectors: {item: p}\ntasks: [t]\n"
    registry = ws({
        "feeds/group/plain/feed.yaml": definition,
        "feeds/pinned/feed.yaml": f"id: {'d' * 32}\nsuffix: pinned\nsource: https://e.test\nselectors: {{item: p}}\n",
        "feeds/other/feed.yaml": "suffix: Taken\nsource: https://e.test\nselectors: {item: p}\n",
    }, startup=True)
    plain = registry.feeds["group/plain"].feed
    assert plain.key == "plain"

    # state kept under the suffix
    storage.store_feed_output("plain", "<rss>stored</rss>", "application/rss+xml", 3)
    filters.filter_history_update(plain, [{"link": "l1"}, {"link": "l2"}])
    Subscription.objects.create(feed_id="plain", topic="t", callback="http://r/cb", verified=True,
                                created_at="2024-01-01T00:00:00Z")
    run = TaskRun.objects.create(task="t", trigger="manual", status="ok", started_at="2024-01-01T00:00:00Z")
    FeedRun.objects.create(task_run=run, feed_id="plain", started_at="2024-01-01T00:00:00Z", status="ok")
    assert (conf.FILES_DIR / "feed" / "plain").exists()  # the store is named by the suffix
    # leftovers of a deleted feed under the target name must not be inherited
    storage.store_feed_output("fresh", "stale", "text/plain", 1)
    HistoryKey.objects.create(namespace=filters.history_namespace("fresh"), key_hash="x", seen_at="2024-01-01T00:00:00Z")

    def rename(path, suffix):
        return post(client, "/feed/rename-suffix", {"path": path, "suffix": suffix})

    assert rename("feeds/group/plain", "новости").status_code == 400  # ASCII only
    assert rename("feeds/group/plain", "a b").status_code == 400
    assert rename("feeds/group/plain", "taken").status_code == 409  # case-insensitively unique
    assert rename("feeds/missing", "x").status_code == 404
    assert rename("feeds/group", "x").status_code == 400  # a folder, not a feed
    assert (ws.root / "feeds/group/plain/feed.yaml").read_text() == definition  # nothing changed so far

    done = rename("feeds/group/plain/feed.yaml", "fresh").json()
    assert done["suffix"] == "fresh" and done["load"]["ok"]
    assert done["moved"] == {"history": 2, "subscriptions": 1, "runs": 1, "output": True}
    text = (ws.root / "feeds/group/plain/feed.yaml").read_text()
    assert text == "title: Plain  # t\nsuffix: fresh\nsource: https://e.test\nselectors: {item: p}\ntasks: [t]\n"
    assert done["content"] == text
    renamed = workspace.get_registry().feeds["group/plain"]
    assert renamed.suffix == "fresh" and renamed.key == "fresh"
    assert client.get("/feed/fresh").content == b"<rss>stored</rss>" and client.get("/feed/plain").status_code == 404
    assert not (conf.FILES_DIR / "feed" / "plain").exists() and not FeedOutput.objects.filter(feed_id="plain").exists()
    assert filters.filter_history(renamed.feed, [{"link": "l1"}, {"link": "new"}]) == [{"link": "new"}]
    assert HistoryKey.objects.filter(namespace=filters.history_namespace("fresh")).count() == 2
    assert Subscription.objects.get().feed_id == "fresh" and FeedRun.objects.get().feed_id == "fresh"

    # a feed with an id keeps its state under the id: only the definition changes
    storage.store_feed_output("d" * 32, "pinned output", "text/plain", 1)
    pinned = rename("feeds/pinned", "moved-url").json()
    assert pinned["moved"] is None and workspace.get_registry().feeds["pinned"].key == "d" * 32
    assert workspace.get_registry().feeds["pinned"].suffix == "moved-url"
    assert client.get("/feed/pinned").status_code == 404  # the old address is gone
    assert storage.fetch_feed_output("d" * 32)["output"] == "pinned output"
    assert rename("feeds/pinned", "moved-url").json()["moved"] is None  # unchanged suffix: a no-op


def test_save_renames_suffix(http, ws, client):
    ws({
        "feeds/plain/feed.yaml": "suffix: plain\nsource: https://e.test\nselectors: {item: p}\n",
        "feeds/pinned/feed.yaml": f"id: {'d' * 32}\nsuffix: pinned\nsource: https://e.test\nselectors: {{item: p}}\n",
        "feeds/other/feed.yaml": "suffix: Taken\nsource: https://e.test\nselectors: {item: p}\n",
    }, startup=True)
    storage.store_feed_output("plain", "<rss>stored</rss>", "application/rss+xml", 3)
    path = "feeds/plain/feed.yaml"
    original = (ws.root / path).read_text()

    clash = put(client, path, "suffix: taken\nsource: https://e.test\nselectors: {item: p}\n")
    assert clash.status_code == 409 and "mtime" not in clash.json()  # not a disk conflict
    assert (ws.root / path).read_text() == original  # refused before writing

    same = put(client, path, "suffix: plain\ntitle: T\nsource: https://e.test\nselectors: {item: p}\n").json()
    assert same["renamed"] is None

    done = put(client, path, "suffix: fresh\nsource: https://e.test\nselectors: {item: p}\n").json()
    assert done["renamed"]["from"] == "plain" and done["renamed"]["to"] == "fresh"
    assert done["renamed"]["moved"]["output"] is True and done["load"]["ok"]
    assert storage.fetch_feed_output("fresh")["output"] == "<rss>stored</rss>"
    assert storage.fetch_feed_output("plain") is None and client.get("/feed/plain").status_code == 404

    # removing the suffix falls back to the directory name
    back = put(client, path, "source: https://e.test\nselectors: {item: p}\n").json()
    assert back["renamed"]["to"] == "plain" and storage.fetch_feed_output("plain")["output"] == "<rss>stored</rss>"

    # a feed with an id keeps its state under the id
    storage.store_feed_output("d" * 32, "pinned output", "text/plain", 1)
    pinned = put(client, "feeds/pinned/feed.yaml",
                 f"id: {'d' * 32}\nsuffix: moved\nsource: https://e.test\nselectors: {{item: p}}\n").json()
    assert pinned["renamed"] == {"from": "pinned", "to": "moved", "moved": None}
    assert storage.fetch_feed_output("d" * 32)["output"] == "pinned output"
    assert put(client, "feeds/pinned/feed.yaml",
               f"id: {'d' * 32}\nsuffix: taken\nsource: https://e.test\nselectors: {{item: p}}\n").status_code == 409


def test_console_api_wordfilters(ws, client):
    ws({"feeds/news/feed.yaml": "source: http://example.com/\nselectors: {item: article}\n"
                                "filter:\n  - wordfilter: spam\n"}, startup=True)
    rows = client.get(f"{API}/wordfilters").json()
    assert rows == [{"name": "spam", "count": 0, "feeds": [{"title": rows[0]["feeds"][0]["title"], "dir": "feeds/news"}],
                     "exists": False}]
    assert client.get(f"{API}/wordfilter", {"name": "spam"}).json()["exists"] is False

    def save(name, text):
        return client.put(f"{API}/wordfilter", data=json.dumps({"name": name, "text": text}),
                          content_type="application/json")

    response = save("spam", "Casino\n\n  /free\\s+money/ \ncasino\n/[bad/\n")
    assert response.status_code == 400
    assert response.json()["diagnostics"] == [{"line": 5, "message": response.json()["diagnostics"][0]["message"]}]
    result = save("spam", "Casino\n\n  /free\\s+money/ \ncasino\n").json()
    assert result == {"name": "spam", "count": 2, "text": "casino\n/free\\s+money/\n"}
    assert filters.list_word_filter("spam") == ["casino", "free\\s+money"]
    assert filters.matches_filter("Get FREE   money", filters.load_matchers("spam"))

    assert post(client, "/wordfilter/create", {"name": "spam"}).status_code == 409
    assert post(client, "/wordfilter/create", {"name": " "}).status_code == 400
    assert post(client, "/wordfilter/create", {"name": "other"}).status_code == 200
    assert [(r["name"], r["count"], r["exists"]) for r in client.get(f"{API}/wordfilters").json()] == [
        ("other", 0, True), ("spam", 2, True)]
    assert post(client, "/wordfilter/rename", {"name": "other", "new_name": "spam"}).status_code == 409
    renamed = post(client, "/wordfilter/rename", {"name": "spam", "new_name": "junk"}).json()
    assert renamed["name"] == "junk" and renamed["feeds"][0]["dir"] == "feeds/news"
    assert filters.list_word_filter("junk") == ["casino", "free\\s+money"]
    assert client.delete(f"{API}/wordfilter?name=junk").status_code == 200
    assert client.delete(f"{API}/wordfilter?name=junk").status_code == 404
    assert not WordFilterEntry.objects.filter(filter__name="junk").exists()


def test_console_api_tasks_table(ws, client):
    ws({
        "feeds/a/feed.yaml": "title: Alpha\nsource: https://e.test\nselectors: {item: p}\ntasks: [news]\ntags: [x]\n",
        "tasks.yaml": "# my tasks\ntasks:\n  morning: {run: [news], timeout: 30m}  # daily\n  empty:\n",
        "schedules.yaml": "schedules:\n  - task: morning\n    cron: '0 6 * * *'\n",
    }, startup=True)
    data = client.get(f"{API}/tasks/table").json()
    rows = {r["name"]: r for r in data["tasks"]}
    assert [r["name"] for r in data["tasks"]] == ["morning", "empty", "news"]
    assert rows["morning"]["run"] == ["news"] and rows["morning"]["timeout"] == "30m"
    assert rows["morning"]["feeds"] == 1 and rows["morning"]["schedules"] == 1
    assert rows["news"]["declared"] is False and rows["empty"]["declared"] is True

    assert post(client, "/tasks/save", {"name": "morning", "fields": {"then": ["empty"], "timeout": ""}}).status_code == 200
    assert post(client, "/tasks/save", {"name": "news", "fields": {"tags": ["x"], "concurrency": "queue"}}).status_code == 200
    assert post(client, "/tasks/save", {"name": "empty", "fields": {"path": ["a*"]}}).status_code == 200
    text = (ws.root / "tasks.yaml").read_text()
    assert "# my tasks" in text and "# daily" in text and "30m" not in text
    registry = workspace.get_registry()
    assert registry.task("morning").then == ["empty"] and registry.task("morning").timeout is None
    assert registry.task("news").declared and registry.task("news").tags == ["x"]
    assert registry.task("news").concurrency == "queue" and registry.task("empty").paths == ["a*"]

    bad = post(client, "/tasks/save", {"name": "morning", "fields": {"timeout": "soon"}})
    assert bad.status_code == 400 and bad.json()["diagnostics"]
    assert post(client, "/tasks/save", {"name": "a b", "fields": {}}).status_code == 400
    assert post(client, "/tasks/save", {"name": "x", "fields": {"bogus": 1}}).status_code == 400
    assert post(client, "/tasks/save", {"name": "fresh", "fields": {}}).status_code == 200
    assert "fresh" in workspace.get_registry().tasks
    assert post(client, "/tasks/delete", {"task": "fresh"}).status_code == 200
    assert post(client, "/tasks/delete", {"task": "fresh"}).status_code == 404
    assert "fresh" not in workspace.get_registry().tasks


def test_subscriptions_edit_and_replace(ws, client):
    ws({"feeds/a/feed.yaml": "title: A\nsource: https://e.test\nselectors: {item: p}\n"}, startup=True)
    now = "2024-01-01T00:00:00Z"
    one = Subscription.objects.create(feed_id="a", topic="t", callback="http://old.test/cb/1", verified=True,
                                      created_at=now)
    Subscription.objects.create(feed_id="a", topic="t", callback="http://old.test/cb/2", created_at=now)
    Subscription.objects.create(feed_id="a", topic="t", callback="http://new.test/cb/2", created_at=now)
    Subscription.objects.create(feed_id="gone", topic="t", callback="http://old.test/x", created_at=now)

    listed = client.get(f"{API}/subscriptions").json()
    assert [s["callback"] for s in listed] == ["http://new.test/cb/2", "http://old.test/cb/1",
                                               "http://old.test/cb/2", "http://old.test/x"]
    assert listed[0]["title"] == "A" and listed[3]["title"] is None

    url = f"{API}/subscriptions/{one.id}"
    assert client.put(url, data=json.dumps({"callback": "http://old.test/cb/2"}),
                      content_type="application/json").status_code == 409
    response = client.put(url, data=json.dumps({"callback": " http://old.test/cb/9 "}),
                          content_type="application/json")
    assert response.status_code == 200 and response.json()["callback"] == "http://old.test/cb/9"
    assert Subscription.objects.get(id=one.id).verified

    preview = post(client, "/subscriptions/replace", {"find": "old.test", "replace": "new.test", "dry_run": True})
    result = preview.json()
    assert len(result["changed"]) == 2 and [s["reason"] for s in result["skipped"]] == ["duplicate callback"]
    assert Subscription.objects.filter(callback__startswith="http://old.test").count() == 3

    post(client, "/subscriptions/replace", {"find": "old.test", "replace": "new.test"})
    assert sorted(Subscription.objects.values_list("callback", flat=True)) == [
        "http://new.test/cb/2", "http://new.test/cb/9", "http://new.test/x", "http://old.test/cb/2"]

    post(client, "/subscriptions/replace", {"find": r"/cb/(\d)", "replace": r"/hook/\1", "regex": True})
    assert Subscription.objects.filter(callback__contains="/hook/").count() == 3
    assert post(client, "/subscriptions/replace", {"find": "(", "regex": True}).status_code == 400

    assert client.delete(url).status_code == 200 and not Subscription.objects.filter(id=one.id).exists()
