# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Feedxcavator 4: a self-hosted, single-user server that turns web pages into feeds (RSS, Atom, JSON Feed, JSON).
It is the Python/Django/Vue port of the Clojure version 3.0.0, which lives in the sibling repo
`D:\dev\rss\feedxcavator`. The specification is `D:\dev\rss\feedxcavator\.local\PORT-REQUIREMENTS.md`, written as
a delta over `REQUIREMENTS.md` in the same folder. Code comments cite requirement IDs from those two documents
(`UC2-8`, `FD2-4`, `TSK2-6a`, `EX-4`); look the ID up there before changing behavior it names.

## Commands

```
.venv\Scripts\python -m pytest                        # all tests (about 15 s)
.venv\Scripts\python -m pytest tests/test_web.py -k handlers
cd frontend && npm run build                          # rebuilds feedxcavator/static/console (committed)
.venv\Scripts\python -m feedxcavator run              # supervisor: web + worker
.venv\Scripts\python -m feedxcavator manage makemigrations feedxcavator
```

The server reads `config.yml`, `data/`, `log/` and `workspace/` from the working directory, or from
`FEEDXCAVATOR_HOME` when set. To run against a throwaway home, set `FEEDXCAVATOR_HOME` and `PYTHONPATH` to this
repo. Never point a test run at a real home directory.

Tests set up their own temporary home and database in `tests/conftest.py`. pytest-django is disabled on purpose
(`-p no:django`): views run in worker threads, which need a file database and real commits.

## Architecture

Two processes from one installation, started and supervised by `cli.py`:

- **web** (`asgi.py`, uvicorn): public routes in `views.py`, the console JSON API in `api.py` (django-ninja,
  under `/front/api/`), access control and CSRF in `middleware.py`.
- **worker** (`worker.py`): the queue poll loop, the cron scheduler, `runner.py` (task runs on two thread pools),
  the browser pool (`browser.py`), Telegram (`telegram.py`), and the RPC server (`rpc.py`).

The web process never runs tasks. It enqueues through the `QueueItem` table. It reaches the worker's browser
pool and Telegram over `rpc.py` (a localhost `multiprocessing.connection`, port and key in `data/worker.rpc`).

Both processes load the workspace independently (`workspace.py`) and reload when files change.

### Request and extraction flow

`views.feed` → `extraction.run_feed(feed, mode)` with mode `request`, `background` or `test`.
`run_feed` calls the feed's extractor or `default_feed_parser`, then `finish_headlines` (transform,
post_process, filter steps, full text, limit), then `output.produce_feed_output`. Background mode stores the output and publishes to WebSub.
A feed with `tasks` is always served from stored output.

### Workspace loading (`workspace.py`)

A load builds a new `Registry` and swaps it in at once. Order: prelude (`userlib.install`), `lib/`, each feed
directory, `handlers/`, then `tasks.yaml` and `schedules.yaml`.

- `lib/` files evaluate directly into the shared namespace. A failure abandons the load and keeps the previous
  registry (at startup there is none, so the rest loads without that file).
- A `lib/` folder in a group folder under `feeds/` (WS2-9) is a group library: one unit, evaluated into a copy of
  the enclosing group's namespace and never merged into the shared one. Feeds in the group start from that copy, so
  `_merge` skips names a feed only inherited. `extends` searches group libs nearest first, then `lib/`
  (`template_loader(root, feed_dir)`); a template's own `extends` searches on from its directory.
- Each feed directory and each handler file is a unit: it evaluates into a copy of the namespace, and its names
  and registrations are merged only if every file succeeds. This is what isolates a broken feed.
- Registrations go through `workspace.register`, called by the decorators in `userlib.py` and the macros in
  `hylib.hy`. Outside a load (scratch, REPL) they are ignored.
- Hy files compile with `hy_compile` into the unit module. `hy_reader()` is a reader subclass that allows dots in
  keywords, needed for Enlive-style selector vectors.

### Other modules

| Module | Role |
|---|---|
| `feeddef.py` | pydantic schema for `feed.yaml`, legacy-spelling normalization, `extends`, the read-only `Feed` mapping |
| `selectors.py` | CSS / `xpath:` / `jmes:` selectors, part options, `apply_selectors`, `fix_relative_url` |
| `fetch.py` | `fetch_url` on curl_cffi: never raises, per-context error state, retries, `Retry-After` |
| `filters.py` | history (`HistoryKey`), word filters, the ordered filter steps |
| `tasks.py` | `tasks.yaml` and `schedules.yaml` models, cycle detection, feed matching |
| `auto.py` | `mode: auto` and "Suggest selectors" |
| `context.py` | contextvars: current feed, logging source, fetch state, run mode, cancel event |
| `storage.py` | atomic file writes, blobs, stored feed output, key-value objects |
| `userlib.py` | everything in the user namespace; the console's API tab is generated from its docstrings |

## Conventions and pitfalls

- **Blocking work must not hold the event loop.** Public views use the `@threaded` decorator in `views.py`, API
  endpoints use `@blocking` in `api.py`. Both run the function in its own thread and close its database
  connections. Do not write plain sync views: Django would run them all on one shared thread.
- **Streaming files** must use an async iterator (`views.file_response`). A sync iterator makes Django read the
  whole file into memory under ASGI.
- **Error handlers** (`handler404`) must be sync functions.
- **Ambient state** is in contextvars. Use `context.scope(...)` rather than globals, and pass the context
  explicitly when starting threads.
- **Adding a user-library function:** define it, add it to `CATEGORIES` in `userlib.py`, and give it a docstring.
  A Hy spelling that mangling cannot produce (`name!`, `a->b`) goes in `HY_ALIASES`.
- **`fetch_url` options:** `timeout` is in milliseconds; `fetch.timeout` in `feed.yaml` is in seconds. In Python,
  `as` and `async` are passed as `as_` and `async_`.
- **Feed identity** is the *key*: the optional `id` (32 hex characters) when the feed sets one, otherwise its
  `suffix`. Everything stored about a feed (output file `data/files/feed/<key>`, history, subscriptions, run
  records, `FeedIndex`) is kept under `feed["key"]` / `FeedInfo.key`; never use `feed["id"]` for that, it is None
  for most feeds. This departs from FD2-2 and WS2-7 of the spec on purpose: the server never writes ids into
  `feed.yaml`, and `/feed/uuid:<id>` works only for feeds that set an id.
- **Suffixes** are ASCII (`feeddef.SUFFIX_RE`) and unique case-insensitively, because stores are files named by
  them. A directory name that is not a valid suffix needs an explicit `suffix`.
- **Changing a suffix** by hand makes it a new feed. The console's "Rename suffix" (`/front/api/feed/rename-suffix`
  → `storage.rename_feed_state`) moves the stored state. A new store keyed by the feed must be added there too.
- An all-digit id is a YAML number, so `feeddef.parse_definition` re-reads it from the text. Keep that when
  touching id handling.
- **lxml elements:** do not use `id(el.getparent())` or truth-test elements; proxies are transient.
- **Workspace writes** from the server go through `storage.write_atomic`. The server never runs git in the
  workspace and writes no caches into it.
- **Model changes** need a migration (`manage makemigrations feedxcavator`); `run`, `web` and `worker` migrate
  at startup.
- **Frontend changes** need `npm run build`; the built assets under `feedxcavator/static/console` are committed.
  `mode-hy.js` lists the prelude names for highlighting and should follow `userlib.py` and `hylib.hy`.
- **Shell:** in Git Bash on this machine, long heredocs that write several files and contain apostrophes fail to
  parse. Write files with the editor tools instead.

## Not implemented or not exercised

- AUTO-5 (LLM selector suggestion) and schema-driven autocompletion are not implemented.
- Telegram has not been run against a real account.
- Linux specifics (Xvfb, the systemd unit, `feedxcavator.sh`), PostgreSQL and the worker REPL are written but
  untested.
- The research evaluations R-1 to R-4 were not run; the README lists the choices made instead.
