"""Records the fixtures of the static demo console (frontend/src/demo/fixtures.json).

It talks to a running server over HTTP, so feed tests run for real, the browser feeds in Camoufox:

    cd demo/home            # config.yml: authentication: false, workspace: ../workspace (not in git)
    FEEDXCAVATOR_HOME=$PWD PYTHONPATH=../.. ../../.venv/Scripts/python -m feedxcavator run
    python tools/record_demo.py [--url http://localhost:21900] [--feeds x-orbital-lab,...] [--no-run]

With --feeds only those feed tests are recorded again; the other recorded tests are kept. --no-run skips running
the `morning` task (which fills the Runs and Log tabs).
"""

import argparse
import functools
import http.cookiejar
import http.server
import threading
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = REPO / "demo" / "workspace"
HOME = REPO / "demo" / "home"
SITES = REPO / "demo" / "sites"  # the fictional pages the browser feeds read
SITES_PORT = 21901
# where GitHub Pages publishes demo/sites: links to the fictional pages in the fixtures point there
SITES_PUBLIC = "https://gchristensen.github.io/feedxcavator/demo/sites/"
OUT = REPO / "frontend" / "src" / "demo" / "fixtures.json"
EDITABLE = {".yaml", ".yml", ".hy", ".py", ".js", ".txt", ".md", ".json", ".css", ".html"}
DEMO_WORKSPACE_PATH = "/srv/feedxcavator/workspace"
WORD_FILTERS = {"noise": "sponsored\n/(?i)^ask hn: who is hiring/\n"}
RUN_TASK = "morning"


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.open(self.base + "/console").read()  # sets the CSRF cookie

    def token(self):
        return next((c.value for c in self.jar if c.name == "feedxcavator_csrftoken"), "")

    def call(self, method, path, body=None, query=None, timeout=600):
        url = self.base + "/front/api" + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if method != "GET":
            # sent by hand: an https hostname in config.yml marks the cookie secure, and the jar keeps it off http
            request.add_header("Cookie", "feedxcavator_csrftoken=" + self.token())
            request.add_header("X-CSRFToken", self.token())
            request.add_header("Referer", self.base + "/console")
        try:
            with self.opener.open(request, timeout=timeout) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(text or "null")
            except ValueError:
                return e.code, {"error": text[:500]}
        return 200, json.loads(text) if text else None

    def get(self, path, **query):
        status, data = self.call("GET", path, query=query or None)
        if status != 200:
            raise RuntimeError(f"GET {path}: {status} {data}")
        return data


def workspace_files():
    files = {}
    for path in sorted(WORKSPACE.rglob("*")):
        if path.is_file() and path.suffix in EDITABLE and not any(p.startswith(".") for p in path.parts):
            files[path.relative_to(WORKSPACE).as_posix()] = path.read_text(encoding="utf-8")
    return files


def scrub(value, needles):
    """Replaces local absolute paths in every string of `value`."""
    if isinstance(value, str):
        for needle, replacement in needles:
            value = value.replace(needle, replacement)
        # what was a Windows path reads as a Unix one: /opt/feedxcavator\feedxcavator\api.py
        return re.sub(r'(/opt/feedxcavator|/srv/feedxcavator|/usr/lib/python3)[^"\n]*',
                      lambda m: m.group(0).replace("\\", "/"), value)
    if isinstance(value, list):
        return [scrub(v, needles) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v, needles) for k, v in value.items()}
    return value


def serve_sites():
    """Serves demo/sites on 127.0.0.1:SITES_PORT for the duration of the recording."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(SITES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", SITES_PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:21900")
    parser.add_argument("--feeds", help="comma-separated feed keys to record again")
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()
    serve_sites()
    client = Client(args.url)
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}

    for name, text in WORD_FILTERS.items():
        client.call("POST", "/wordfilter/create", {"name": name})
        client.call("PUT", "/wordfilter", {"name": name, "text": text})

    feeds = client.get("/feeds")
    only = set(args.feeds.split(",")) if args.feeds else None
    tests = dict(old.get("tests", {})) if only else {}
    for feed in feeds:
        if only and feed["key"] not in only:
            continue
        path = feed["dir"] + "/feed.yaml"
        yaml = (WORKSPACE / path).read_text(encoding="utf-8")
        started = time.monotonic()
        status, result = client.call("POST", "/feed/test", {"path": path, "yaml": yaml})
        if status != 200:
            result = {"output": "", "content_type": "text/plain", "count": 0,
                      "error": (result or {}).get("error", f"HTTP {status}")}
        result["seconds"] = round(time.monotonic() - started, 1)
        tests[path] = result
        print(f"{feed['key']}: {result['count']} items, error={result['error']!r}, {result['seconds']} s")

    files = workspace_files()
    scratch = old.get("scratch", {})
    if not only or "scratch" in only:
        status, scratch = client.call("POST", "/scratch/run", {"path": "scratch.hy", "content": files["scratch.hy"]})
        print("scratch:", status, str(scratch)[:200])

    if not args.no_run and not only:
        client.call("DELETE", "/runs")  # only this run in the Runs and Log tabs
        client.call("DELETE", "/log")
        time.sleep(30)  # Hacker News answers 503 to a second request right after the test
        client.call("POST", "/tasks/run", {"task": RUN_TASK})
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            runs = client.get("/runs", limit=5)
            if runs and runs[0]["task"] == RUN_TASK and runs[0]["status"] != "running":
                print("run:", runs[0]["status"])
                break
            time.sleep(3)
        else:
            print("run: still running after 15 minutes; recording it as it is")

    wordfilters = client.get("/wordfilters")
    fixtures = {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "settings": client.get("/settings"),
        "feeds": feeds,
        "files": files,
        "tests": tests,
        "scratch": scratch,
        "tasks": client.get("/tasks"),
        "tasks_table": client.get("/tasks/table"),
        "schedules": client.get("/schedules"),
        "wordfilters": wordfilters,
        "wordfilter_texts": {w["name"]: client.get("/wordfilter", name=w["name"])["text"] for w in wordfilters},
        "runs": client.get("/runs", limit=100) if not only else old.get("runs", []),
        "log": client.get("/log", n=300) if not only else old.get("log", []),
        "reference": client.get("/reference"),
        "manual": client.get("/manual"),
    }
    fixtures["settings"]["workspace"] = DEMO_WORKSPACE_PATH
    home = str(HOME.resolve())
    needles = [(str(WORKSPACE.resolve()), DEMO_WORKSPACE_PATH), (home, "/srv/feedxcavator"),
               (str(REPO.resolve()), "/opt/feedxcavator"), (str(Path(sys.base_prefix).resolve()), "/usr/lib/python3"),
               (sys.base_prefix, "/usr/lib/python3")]
    needles += [(a.replace("\\", "/"), b) for a, b in needles] + [(a.replace("\\", "\\\\"), b) for a, b in needles]
    needles.append((f"http://127.0.0.1:{SITES_PORT}/", SITES_PUBLIC))
    fixtures = scrub(fixtures, needles)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixtures, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
