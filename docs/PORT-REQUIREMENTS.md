# Feedxcavator port requirements (Python/Django/Vue)

This document describes the port of Feedxcavator 3.0.0 to Python, Django, and Vue. It is written as a delta against
`REQUIREMENTS.md`: every requirement there still applies unless this document changes, replaces, or drops it. References
such as FD-7 or §17.4 point into `REQUIREMENTS.md`. New requirements carry their own IDs.

"Must" and "should" have the same meaning as in `REQUIREMENTS.md`. Items marked **[research]** depend on an evaluation
that has not been done yet (section 19).

## 1. Decisions

| #   | Decision                                                                                                              |
|-----|-----------------------------------------------------------------------------------------------------------------------|
| D-1 | User code and feed definitions live as files on disk in a workspace directory, not in the database.                   |
| D-2 | A broken feed file disables only that feed. A broken shared library file keeps the previous loaded state running.     |
| D-3 | Background feeds store their rendered output document, as now. Individual items are not stored.                       |
| D-4 | Browser automation is required in the first release, because existing extractors depend on it.                      |
| D-5 | The console keeps the Ace editor, upgraded to the current 1.x release.                                                |
| D-6 | EDN output is dropped. JSON remains for aggregation.                                                                  |
| D-7 | User code can be written in Python or Hy. Both share one namespace.                                                   |
| D-8 | Tasks and schedules are declared in YAML files in the workspace. Schedules are managed on their own console tab.     |
| D-9 | The browser is used only when a feed or user code asks for it explicitly. There is no automatic fallback to it.      |
| D-10| Telegram support (INT-3) is required in the first release.                                                            |
| D-11| The server never commits to git, even when the workspace is a git repository.                                         |
| D-12| Full-text results exist only in the rendered output. They are not cached separately.                                  |
| D-13| The console has one workspace tree for all files. There are no separate Library, Handlers, Extractors, or Tasks tabs. |
| D-14| A feed is identified by its suffix. The `id` field is optional, and the server never writes to a feed definition.     |

## 2. Stack

STK-1. Server: Python 3.12 or later, Django, served by an ASGI server (uvicorn or granian).

STK-2. Console API: JSON over HTTP, implemented with django-ninja (or DRF if there is a reason to prefer it).

STK-3. Console: Vue 3 built with Vite. The built assets are served as static files. No build step is needed at run time
(UI requirements in §16 otherwise unchanged).

STK-4. Database: SQLite in WAL mode by default. PostgreSQL must also work through configuration alone.

STK-5. HTTP fetching: curl_cffi (FT-*, section 9).

STK-6. HTML and selectors: parsel on lxml (section 8).

STK-7. Hy: Hy 1.x with hyrule.

STK-8. Feed output: `django.utils.feedgenerator` for RSS and Atom (section 13).

## 3. Process architecture

PRC-1. The application runs as two processes from one installation: a **web** process (ASGI, serves HTTP) and a
**worker** process (scheduler, task worker pools, browser pool). A single command starts both and supervises them.

PRC-2. Both processes load the workspace (section 4) independently. Each must reload when workspace files change
(UC2-6).

PRC-3. Feed jobs, task runs, and schedule firings happen only in the worker. The web process enqueues tasks through the
database (or the chosen queue backend, R-3) and never blocks on them.

PRC-4. A console test run (API-6) or a request for a feed without a `task` (EX-4) runs the extraction in the web
process. When such an extraction needs a browser, the web process asks the worker's browser pool to run it.

PRC-5. CFG-7 still applies: a stalled client must not block other requests. Long-running request handlers must not
hold the event loop. Synchronous user code runs in a thread pool.

PRC-6. LFC-1 to LFC-5 still apply to the pair of processes. Child-process tracking (LFC-4) covers browsers started by
the worker.

## 4. Workspace

WS2-1. The workspace is a directory, by default `workspace/` next to `config.yml`. Its location is configurable
(`workspace` key).

WS2-2. Layout:

```
workspace/
  lib/                     shared code, loaded first
    _order.yaml            optional explicit load order
    forums.hy
    util.py
  feeds/
    news/local/city-paper/ one directory per feed
      feed.yaml
      extract.hy           optional feed-local code
    video/channel-x/
      feed.yaml
    video/lib/             optional group library: code and templates for feeds under video/ (WS2-9)
  handlers/
    telegram-hook.py
  tasks.yaml
  schedules.yaml
  scratch.hy               never loaded automatically
```

WS2-3. A feed is a directory under `feeds/` that contains `feed.yaml`. The path of the directory relative to `feeds/`,
minus its last component, is the feed's group. The `group` field (§3.2) is removed. The console tree mirrors the
directory tree.

WS2-4. Every `.py` and `.hy` file in a feed directory is feed-local code for that feed.

WS2-5. The console reads and writes workspace files through the API. Edits made outside the console (an IDE, git) must
be picked up the same way as console saves.

WS2-6. Writes from the console must be atomic (temporary file in the same directory, then rename, as STO-4).

WS2-7. Moving or renaming a feed directory must not change the identity (FD2-2) of a feed that has an explicit `suffix`
or an `id`. A feed whose suffix defaults to the directory name takes a new identity when the directory is renamed.

WS2-8. The workspace should work as a git repository. The server must not create or modify files in it other than the
ones the user asks for (new feed, saved file, renamed suffix), and must not write caches or compiled output into it.
It must never run git commands in the workspace (D-11).

WS2-9. A group folder under `feeds/` (`feeds/` itself included) may contain a `lib/` directory that is not a feed (has
no `feed.yaml`): the group library. It is not searched for feeds.

- Its `.py` and `.hy` files, ordered as in UC2-3 (its own `_order.yaml`, otherwise alphabetically by path), load as
  one unit into a copy of the namespace of the enclosing group (for the outermost group, the shared namespace), on
  first use, before the code of the first feed in the group. Feeds of the group and of its nested groups start from
  that namespace. Names defined in a group library do not enter the shared namespace; registrations (extractors,
  handlers, task functions, proxies) are global, as from any other file.
- If a group library fails to evaluate, the error is reported and every feed in the group and its nested groups is
  disabled as in UC2-8, keeping its identity. The rest of the workspace loads normally.
- It may hold templates: `extends` looks in the group libraries of the feed, nearest first, then in `lib/` (FD2-6).

## 5. User code

### 5.1 Languages

UC2-1. A user code file is Python (`.py`) or Hy (`.hy`). Both are evaluated into one shared global namespace, so a Hy
file can call a function defined in a Python file and the other way round.

UC2-2. Before any user file, the server evaluates a built-in prelude that provides the user library (UC-7) and, for
Hy, the macros in section 6.

### 5.2 Load order

UC2-3. A full load evaluates, in this order:

1. the prelude,
2. `lib/`, in the order given by `lib/_order.yaml`, otherwise alphabetically by path,
3. the code files of each feed, feeds in alphabetical order of path, files within a feed alphabetically; the group
   libraries of a feed (WS2-9) load before its first feed,
4. `handlers/`, alphabetically.

`tasks.yaml` and `schedules.yaml` are read after the code, so they can refer to functions defined in it (TSK2-6).

UC2-4. Feed-local code may rely on the prelude, `lib/` and the libraries of its groups (WS2-9). Relying on names defined by another feed's files is
unsupported, because load order between feeds is not a contract.

### 5.3 Isolation of failures

UC2-5. A full load builds a new namespace and a new registry (extractors, handlers, proxies, tasks, schedules) without
touching the live ones, then swaps them in at once. Requests and jobs already running finish with the state they
started with.

UC2-6. A full load runs at startup (fixing §17.1) and whenever a workspace file changes. File changes are debounced (for
example 500 ms) so that a save touching several files causes one load.

UC2-7. If a `lib/` file fails to evaluate, the load is abandoned and the previous namespace stays live. The error is
logged and reported to the console. The prelude failing is a fatal startup error.

UC2-8. If a feed's code file fails to evaluate, only that feed is disabled: none of its names enter the shared
namespace, it is skipped by tasks, its feed URL returns the last stored output (or 503 if there is none), and the
console marks it with the error. All other feeds load normally.

UC2-9. To make UC2-8 possible, each feed's files are evaluated into a shallow copy of the shared namespace. Only when
all of that feed's files succeed are the names they added or changed merged back into the shared namespace and their
registrations committed to the registry.

UC2-10. A handler file that fails disables only the handlers defined in it. Their routes return 503 until it is fixed.

UC2-11. Errors must carry the workspace-relative path and the line number. Source text is registered with `linecache`
under that path so that tracebacks at run time also point into the workspace.

### 5.4 Registry

UC2-12. User code registers constructs by decorator (Python) or macro (Hy), not by bare names:

| Construct | Python                               | Hy                                    |
|-----------|--------------------------------------|---------------------------------------|
| Extractor | `@extractor("name")`                 | `(defextractor name [feed] ...)`      |
| Handler   | `@handler("name", auth=False)`       | `(defhandler ^:auth name [a b] ...)`  |
| Proxy     | `proxy("name", host=, port=, ...)`   | `(defproxy name :host ... :port ...)` |
| Task fn   | `@task_function("name")`             | `(deftaskfn name [] ...)`             |

UC2-13. Registering a name twice is an error. The second registration fails, and the file that made it is treated as
failed (UC2-8, UC2-10). The error message names both files.

UC2-14. If a feed directory's code defines exactly one extractor and `feed.yaml` has no `extractor` field, that extractor
is used for the feed.

UC2-15. `deftask*` and the `schedule*` macros (UC-6, SCH-1) are removed. Tasks and schedules come from YAML (sections
11, 12).

### 5.5 Data conventions

UC2-16. Headlines are plain dicts with string keys in snake_case: `title`, `link`, `summary`, `content`, `author`,
`category`, `image`, `image_type`, `guid`, `published`, plus any extra fields. `html` holds the parsel selector of the
item and is never serialized (FD-15).

UC2-17. `category` may be a string or a list of strings.

UC2-18. `published` is optional. When present it is a timezone-aware datetime or an ISO 8601 string, and the RSS output
uses it instead of the synthetic dates of EX-8.

UC2-19. The feed passed to an extractor is a read-only mapping with attribute access (`feed.source`, `feed.params`) and
dict access, including the extra fields (FD-2).

### 5.6 Scratch

UC2-20. `scratch.hy` (or `scratch.py`) is run only when the user runs it from the console. It sees the live namespace,
runs in an isolated copy of it, and returns everything it printed (UC-4). It never changes the live namespace.

### 5.7 Library

UC2-21. The user library of UC-7 is kept, with Python names in snake_case (`fetch_url`, `filter_history`) and the Hy
spellings available through mangling (`fetch-url`). The functions bound to EDN are removed. The API tab (UI-19) is
generated from the docstrings of the library so it cannot fall out of step.

## 6. Hy DSL

HY-1. The prelude makes these macros available to every Hy file without an explicit `require`: `defextractor`,
`defhandler`, `defproxy`, `deftaskfn`, `with-browser`, `log-fetch-errors`, `safe-call`, `safely-repeat`, plus the
selector macros of section 8.3.

HY-2. The prelude provides Clojure-style helpers over plain dicts and sequences: `assoc`, `dissoc`, `update`, `get-in`,
`assoc-in`, `update-in`, `merge`, `select-keys`, `distinct-by`, `keep`, `mapcat`, `partition`, `group-by`, `frequencies`.
They return new values and do not mutate their arguments.

HY-3. Threading macros (`->`, `->>`, `as->`, `cond->`, `some->`) are available. Keyword lookup on dicts (`(:title h)`)
must work on headline dicts.

HY-4. Source compatibility with the Clojure code of 3.0.0 is not a goal. The DSL should keep existing extractors close
enough to port line by line.

## 7. Feed definition

### 7.1 Identity and storage

FD2-1. `feed.yaml` is stored on disk verbatim, comments included (replaces FD-1). The server never rewrites a definition
on its own. The only change it makes is to the `suffix` line, when the user renames the suffix from the console
(FD2-3b).

FD2-2. A feed is identified by its **key**: its `id` when the definition has one, otherwise its `suffix` (replaces
FD-3, D-14). Everything stored about a feed outside the workspace is kept under the key: stored output, history,
subscriptions, run records, and the feed index.

FD2-2a. `id` is optional. When present it is 32 lowercase hex characters. A feed with an `id` keeps its stored state
across any change of suffix or directory, and is also served at `/feed/uuid:<id>`. The server never generates or
inserts an id, and "New Feed" does not write one. Duplicate ids are an error that disables the later feed. An id made
only of digits must be read as a string even when it is written unquoted.

FD2-3. `suffix` must be unique, compared case-insensitively because stores are files named by it. A duplicate disables
the later feed with an error (fixes the unchecked uniqueness in §3.2). A suffix that equals the `id` of another feed is
the same error. When `suffix` is absent it defaults to the feed directory's name.

FD2-3a. A suffix may contain only ASCII letters, digits, `.`, `-`, `_`, and `~`, and must start with a letter or a
digit. A definition with any other suffix fails validation. A feed without `suffix` whose directory name is not a
valid suffix is disabled with an error that asks for an explicit `suffix`.

FD2-3b. Changing `suffix` in the file gives the feed a new key, unless it has an `id`: history starts empty and the
state stored under the old suffix is no longer used. The console's "Rename suffix" action (UI2-7) changes the suffix
and keeps the state:

- It rewrites only the `suffix` line of `feed.yaml`, keeping a trailing comment. When the suffix was defaulted from
  the directory name, it adds the line after `title`.
- For a feed without `id` it moves the stored output, history, subscriptions, and run records to the new suffix.
  State left under the new suffix by a feed that no longer exists is discarded first, not inherited.
- For a feed with `id` it changes only the definition.
- It refuses a suffix that is invalid (FD2-3a) or already in use (FD2-3), and a feed that is not loaded.

FD2-4. Definitions are validated against a schema (pydantic). The schema is exported as JSON Schema for the editor
(UI2-5). Validation and YAML syntax errors return 400 with the messages and their line numbers (replaces FD-4's 500).

### 7.2 Fields

FD2-5. Top-level fields:

| Field        | Type                 | Default  | Meaning                                                                         |
|--------------|----------------------|----------|---------------------------------------------------------------------------------|
| `id`         | string               | none     | Optional (FD2-2a).                                                              |
| `title`      | string               | none     | As before.                                                                      |
| `suffix`     | string               | dir name | FD2-3, FD2-3a. The identity of a feed without `id`.                             |
| `enabled`    | boolean              | true     | `false` excludes the feed from tasks and makes its URL return 404.              |
| `tags`       | list of strings      | none     | Used to select feeds in tasks (TSK2-2).                                         |
| `extends`    | string               | none     | Template to inherit from (FD2-6).                                               |
| `source`     | string or list       | none     | One or more source URLs. Headlines from several sources are concatenated.     |
| `mode`       | `selectors` / `auto` | see text | `auto` uses automatic extraction (section 10). Default is `selectors` when `selectors` is present, otherwise none. |
| `fetch`      | map                  | none     | FD2-8.                                                                          |
| `selectors`  | map                  | none     | Section 8.                                                                      |
| `pages`      | map                  | none     | FD2-9.                                                                          |
| `filter`     | list                 | none     | FD2-10.                                                                         |
| `fulltext`   | boolean or map       | false    | Section 10.                                                                     |
| `transform`  | string               | none     | Name of a function applied to each headline after extraction and before filtering. |
| `output`     | string or map        | `rss`    | `rss`, `atom`, `json-feed`, `json`. Map form: `{format, limit}`.                |
| `tasks`      | list of strings      | none     | Tasks this feed belongs to (replaces the single `task`).                        |
| `parallel`   | boolean              | true     | As before (TSK-6).                                                              |
| `websub`     | boolean or map       | false    | `{enabled, partition}`. Replaces `realtime` and `partition`.                    |
| `extractor`  | string               | none     | As before, plus UC2-14.                                                         |
| `params`     | any                  | none     | As before.                                                                      |

Other top-level keys remain available to extractors as extra fields (FD-2). `charset`, `proxy`, and `timeout` move into
`fetch`. `task` (singular) is still accepted and treated as a one-element `tasks`.

FD2-6. `extends: <path>` names a YAML template under `lib/` (for example `extends: forums/phpbb` reads
`lib/forums/phpbb.yaml`), or under the `lib/` of one of the feed's groups (WS2-9), the nearest found first. The
feed's own keys are deep-merged over the template's: maps merge, other values replace. Templates may extend other
templates; a template's `extends` is looked up from the directory the template is in outward, skipping templates
already in the chain, so a group template can extend a template of the same name in `lib/`. Cycles are an error. A template is not a feed and has no `id`.

FD2-7. The "New Feed" template (FD-6) is updated to the new fields, with only `title`, `suffix`, and `source` active.
`id` appears as a commented-out example.

FD2-8. `fetch` block:

| Key           | Default       | Meaning                                                                          |
|---------------|---------------|----------------------------------------------------------------------------------|
| `backend`     | `http`        | `http` (curl_cffi) or `browser` (stealth browser). No automatic fallback (D-9). |
| `impersonate` | `chrome`      | curl_cffi browser profile.                                                       |
| `method`      | `GET`         |                                                                                  |
| `headers`     | none          |                                                                                  |
| `cookies`     | none          |                                                                                  |
| `charset`     | auto          | Taken from the response, then from the document, then UTF-8. Fixes §17.4.       |
| `proxy`       | none          | Name of a proxy.                                                                 |
| `timeout`     | 30            | Seconds.                                                                         |
| `retry`       | 2             | As FT-1.                                                                         |
| `wait_for`    | none          | Browser backend: CSS selector to wait for before reading the page.               |
| `scroll`      | 0             | Browser backend: number of scrolls to the bottom, for infinite lists.            |

FD2-9. `pages` keeps the numeric form of §3.4 with `{n}` as the placeholder (`%n` still accepted), and adds a
follow-next form:

```yaml
pages:
  next: "a.next::attr(href)"
  max: 5
  delay: 1
```

FD2-10. `filter` becomes an ordered list of steps, run in order:

```yaml
filter:
  - history: { key: link, keep: 500 }   # or keep_days: 30
  - dedupe: link
  - wordfilter: { name: default, fields: [title, summary] }
  - exclude: { field: title, regex: "^Sponsored" }
  - limit: 50
```

The map form of 3.0.0 (`history`, `content`, `wordfilter`) is still accepted and converted to this list. FD-20 still
applies: a console test skips `history` steps.

FD2-11. History keeps the last `keep` keys (default 1000) or the keys seen in the last `keep_days` days, instead of
only the previous batch (changes FD-18). Headlines whose key is in the history are dropped.

## 8. Selectors

### 8.1 Engine

SEL-1. Selectors are evaluated by parsel. CSS is translated by cssselect, so everything in FD-11 works, plus parsel's
extensions.

SEL-2. A part's value is the inner HTML of the first match (FD-8). `link` and `image` take `href` and `src` by default.
`::text` returns the text content, `::attr(name)` an attribute.

SEL-3. The 3.0.0 attribute syntax `img::src-lazy||src` must still work. It means: the first of these attributes present
on the first matching element (FD-9).

SEL-4. A selector starting with `xpath:` is evaluated as XPath. This replaces the native Enlive selectors of FD-12.

SEL-5. When the response is JSON, a selector starting with `jmes:` is evaluated as a JMESPath expression. `item`
selects the list and the other parts are evaluated on each element.

SEL-6. `:root` refers to the item element itself (FD-10). FD-13 (relative URLs) and FD-14 (blank items dropped) still
apply. Protocol-relative URLs take the scheme of `source` (fixes §17.12).

### 8.2 Part options

SEL-7. A part is either a selector string or a map:

```yaml
selectors:
  item: article.post
  title: h2
  link: "a::attr(href)"
  published: { css: "time::attr(datetime)", parse: date }
  category: { css: ".tags a::text", many: true }
  summary: { css: ".preview", strip: true, default: "" }
  image: { css: "img::attr(data-src)||src", regex: "^(.*?)\\?" }
```

Map keys: `css` or `xpath` or `jmes`, `many` (all matches as a list), `strip`, `regex` (first group), `parse` (`date`,
`int`, `text`), `default`.

SEL-8. `category` is extracted by the built-in extractor and written once per value in RSS (fixes §17.3). Any part name
other than the known fields is extracted into an extra headline field.

### 8.3 Selectors in code

SEL-9. Python helpers, all taking a parsel selector, response, or HTML string:

| Function                | Returns                                          |
|-------------------------|--------------------------------------------------|
| `q(node, sel)`          | list of matches                                  |
| `q1(node, sel)`         | first match or `None`                            |
| `text(node)`            | text content                                     |
| `attr(node, name)`      | attribute value                                  |
| `inner_html(node)`      | inner HTML                                       |
| `outer_html(node)`      | outer HTML                                       |
| `extract(node, **parts)`| headline dict, same rules as the YAML `selectors` |
| `apply_selectors(doc, feed)` | headlines, as the built-in extractor         |

SEL-10. Hy macros on top of these:

```hy
($ page "div.post")                       ; all matches
($1 item "h2::text")                      ; first match
(-> page ($1 "article") ($1 "h2") text)
(defselectors post :item "div.post" :title "h2" :link "a::attr(href)")
(post page)                               ; -> headlines
```

SEL-11. A selector given as a literal string to `$`, `$1`, or `defselectors` is compiled once at macro expansion time.

SEL-12. `$` and `$1` also accept an Enlive-style vector, converted to CSS at expansion time, so old snippets stay
readable: `($ page [:div.post :> :a])` is `($ page "div.post > a")`.

## 9. Fetching and browser automation

### 9.1 HTTP

FT2-1. `fetch_url` keeps the contract of FT-1 to FT-6 (never throws, per-request error state, retries with jitter,
`Retry-After`, `log_fetch_errors`). The per-request state is held in a `contextvars` context so it is isolated per
request and per job in both threaded and async code.

FT2-2. `fetch_url` is backed by curl_cffi with browser TLS and HTTP/2 fingerprint impersonation. The profile comes from
the `impersonate` option, then the feed's `fetch.impersonate`, then `chrome`.

FT2-3. `as` accepts `html` and `xml` (parsel selector), `json`, `string`, `bytes`. `edn` is not supported.

FT2-4. `fetch_url` never starts a browser. A browser is used only when the feed sets `fetch.backend: browser` or user
code calls `browser()` (BRW-2) (D-9). A response that looks like a bot challenge is reported as an ordinary HTTP error.

### 9.2 Browser

BRW-1. A stealth browser is required in the first release (D-4). The engine is chosen by R-1. Candidates: Camoufox,
Patchright, nodriver/zendriver, SeleniumBase UC/CDP mode. **[research]**

BRW-2. Browser access goes through one interface, so the engine can be replaced without changing user code:

```python
with browser(profile="default") as b:
    page = b.open(url, wait_for="article")
    html = page.html()
    headlines = page.apply_selectors(feed)
```

`with-browser` and `apply-selectors` from INT-1 map to this interface. `page.apply_selectors` follows section 8 on the
live DOM, returning text content where the built-in extractor gives inner HTML (as INT-1).

BRW-3. Browsers run in a pool owned by the worker (PRC-1), with a configurable size (default 1) and persistent named
profiles under `data/browser/<profile>`. A job waits for a free browser instead of starting extras.

BRW-4. On Linux the browser should run headful under a virtual display (Xvfb) when one is available, because headful
mode is harder to detect. Headless must remain an option.

BRW-5. After the browser passes a challenge, its cookies and user agent are handed to a curl_cffi session with a
matching impersonation profile, and later `fetch_url` calls for that host reuse the session until it is rejected.

BRW-6. Browser processes are child processes for LFC-3 and LFC-4.

BRW-7. INT-2 (the headless HTTP client with optional JS) is replaced by BRW-1 to BRW-5.

### 9.3 Telegram

TG-1. Telegram support is required (D-10). INT-3 applies in full: `telegram.get_channel_messages(channel)` returns
headlines for the most recent messages of a public channel (up to 26), with albums merged, Markdown rendered to HTML,
bare URLs linked, `#` on words replaced with `_`, photos and video thumbnails saved to the blob store (STO2-11) and
appended as `<img>` tags, permalinks as links, and service messages skipped.

TG-2. `telegram.notify(message, chat_id)` sends a text message.

TG-3. Telegram calls are serialized, as in INT-3. A call routes through the proxy chosen by TG2-1.
Whether the client is started and stopped per call (as in 3.0.0) or kept running in the worker is decided with R-4.

TG2-1. A call uses the proxy named by its `proxy` argument (`False` for a direct connection), otherwise the
`fetch.proxy` of the calling feed, otherwise the proxy named `default` when `telegram.default-proxy` is true (false by
default), otherwise a direct connection. The proxy is chosen in the calling process, so a call forwarded by TG-4 uses
the proxy of the feed that made it.

TG-4. The Telegram client runs in the worker (PRC-1). Calls from the web process (a console test) are forwarded to it,
as for the browser (PRC-4).

TG-5. Telegram is inactive when it is not configured: `get_channel_messages` returns nothing and `notify` does nothing,
and both log a warning.

TG-6. The session is stored under `data/telegram/`. A management command performs the interactive login (phone number
and code) and creates the session.

TG-7. The client library is chosen by R-4. If it cannot read the 3.0.0 TDLib session in `data/tddb`, logging in again
with TG-6 is acceptable.

## 10. Automatic extraction

AUTO-1. `mode: auto` extracts headlines without selectors by trying, in order, and stopping at the first that gives
results:

1. an existing feed advertised by the page (`<link rel="alternate">` or common paths), parsed and normalized,
2. structured data in the page (JSON-LD `ItemList`, `Article`, `BlogPosting`, microdata, OpenGraph), via extruct,
3. detection of the repeated item structure in the page, with guessed title, link, image, date, and summary.

AUTO-2. The console's feed editor has a "Suggest selectors" action that runs step 3 (and step 2 where it maps to
selectors) on the current `source` and inserts the proposed `selectors` block into the editor. Nothing is saved until
the user saves.

AUTO-3. `fulltext: true` replaces each headline's `summary` (or fills `content`, with `fulltext: {field: content}`) with
the main text of the linked page, extracted by trafilatura. Full text is fetched after filtering, so with a `history` filter
only headlines that are new in this run are fetched. The results exist only in the rendered output (D-12).
`fulltext: {limit: N}` bounds the number of fetches per run. A page that cannot be fetched or extracted leaves the
headline unchanged.

AUTO-4. The libraries for steps 1 to 3 and for full text are chosen by R-2. Candidates: trafilatura (feed discovery,
main text), extruct, htmldate, newspaper4k, readability-lxml, autoscraper, Scrapling's adaptive matching. **[research]**

AUTO-5. An LLM-based selector suggestion should be considered as an optional, separately configured backend for AUTO-2.
It must be off by default and must never run during scheduled extraction.

## 11. Tasks

TSK2-1. Tasks are declared in `workspace/tasks.yaml`:

```yaml
tasks:
  news:
    feeds: { tags: [news] }
  local:
    feeds: { path: "news/local/*" }
  morning:
    pre: refresh_login_cookies
    run: [news, articles, papers]
    post: notify_summary
    then: [aggregate, remind-me]
    concurrency: skip
    timeout: 30m
  cleanup:
    call: purge_old_objects
```

TSK2-2. A task selects feeds by any combination of `feeds.tags`, `feeds.path` (glob over the feed path), and
`feeds.list` (suffixes). In addition, every name in a feed's `tasks` field is a task containing that feed (TSK-1), with
or without an entry in `tasks.yaml`.

TSK2-3. `run` lists subtasks. Subtasks may themselves have `run`, so supertasks nest (fixes §17.15). A cycle is a load
error that disables the tasks involved.

TSK2-4. `then` lists on-completion tasks, enqueued in order when every feed job of the task and its subtasks has
finished (TSK-5).

TSK2-5. `concurrency` controls overlapping runs of the same task: `skip` (default: do not start if a run is active),
`queue` (start after the active run finishes), `allow` (as 3.0.0, SCH-8).

TSK2-6. `call` names a function registered with `@task_function` / `deftaskfn`, run as one job. This covers what YAML
cannot express.

TSK2-6a. `pre` names a task function (or a list of them, run in order) that runs once in the worker before any feed
job or subtask of the run starts. Typical uses: logging in and storing cookies, warming a browser profile, checking
that a source is up.

- The function receives a task context: the task name, the trigger (STO2-6), and the list of feeds selected for the
  run (including those of subtasks).
- If it returns `False`, the run is skipped and recorded with status `skipped`. Any other return value is ignored.
- If it raises, the run is recorded as `failed`, no feed jobs start, and `then` tasks are not enqueued.
- A subtask's own `pre` runs when that subtask starts, after the parent's `pre`.
- `pre` counts toward `timeout`.

TSK2-6b. `post` names a task function (or a list of them, run in order) that runs once in the worker after every feed
job and subtask of the run has finished, and before the `then` tasks are enqueued. Typical uses: aggregating results,
sending a notification, cleaning up a browser profile or session.

- The function receives the task context of TSK2-6a plus the results of the run: per feed, the status, headline count,
  elapsed time, and error (as recorded in FeedRun, STO2-6).
- `post` runs whether the feed jobs succeeded or failed. It does not run when the run was skipped or failed in `pre`.
- If it raises, the error is recorded on the TaskRun, the run's status becomes `failed`, and `then` tasks are not
  enqueued.
- When the run hits `timeout`, the remaining feed jobs are cancelled (TSK2-7) and `post` still runs, with those feeds
  marked as timed out. `post` itself is bounded by a separate `post_timeout` (default 5 minutes).
- A subtask's own `post` runs when that subtask finishes, before the parent's `post`.

TSK2-6c. Order of a run: `pre`, then feed jobs and subtasks, then `post`, then `then`.

TSK2-7. `timeout` bounds a task run. Feed jobs still running when it expires are cancelled and recorded as timed out.

TSK2-8. Each task run and each feed job is recorded (STO2-6). The profiling log entries of TSK-9 are replaced by these
records and the run history in the console (UI2-8). `profiling-ignore` (UC-9) becomes a per-feed `expect_empty: true`
field that excludes the feed from the zero-headline warning.

TSK2-9. TSK-6 (parallel pool of 6, sequential pool of 1), TSK-7, TSK-8, TSK-10, and TSK-11 still apply. The pool sizes
become configuration keys.

## 12. Scheduling

SCH2-1. Schedules are declared in `workspace/schedules.yaml`:

```yaml
schedules:
  - task: news
    cron: "0 12 * * *"
  - task: digest
    cron: "30 8 * * mon"
    timezone: Europe/Kyiv
    misfire: run-once
  - task: report
    cron: "0 6 15 * *"
    enabled: false
```

SCH2-2. `cron` is a five-field cron expression. `timezone` defaults to the server's local zone (SCH-2). `enabled`
defaults to true. `jitter` (seconds) optionally delays each firing by a random amount up to that value.

SCH2-3. `misfire` sets what happens when a firing was missed because the worker was down: `skip` (default, as SCH-4) or
`run-once` (run once at startup if at least one firing was missed).

SCH2-4. SCH-5 (duplicates ignored), SCH-6 (reload replaces all timers), and SCH-8 (firing enqueues like TSK-8, subject
to TSK2-5) still apply. SCH-7 (`/backend/reschedule`) is kept for compatibility and triggers a reload of
`schedules.yaml`. SCH-9 (`schedule-periodically`) is dropped because cron covers it.

SCH2-5. The time of the last firing of each schedule is stored in the database (STO2-7), so that `misfire: run-once` can
work and the console can show it.

SCH2-6. The scheduler engine is chosen by R-3 (APScheduler in the worker, or a database-backed queue such as django-q2
or Huey). **[research]**

## 13. Output

OUT-1. RSS 2.0 and Atom are generated with `django.utils.feedgenerator`, subclassed to add what EX-8 requires:
`atom:link rel="self"` and, for WebSub feeds, `rel="hub"`; `xml:base`; enclosures; `content:encoded` for `content`
(replacing the non-standard `content` element of EX-8).

OUT-2. Item dates follow EX-8 (decreasing by 5 seconds) unless the headline has `published` (UC2-18).

OUT-3. JSON Feed output is version 1.1, otherwise as EX-9. It is generated directly, without a library.

OUT-4. `json` output is as EX-10. `edn` output is removed (D-6). A feed definition with `output: edn` fails validation.

OUT-5. `output.limit` truncates the headline list before serialization.

OUT-6. Background output is stored as a rendered document (D-3) in `data/files/feed/<key>`, where the key is the
feed's suffix, or its `id` when it has one (FD2-2). Its content type and generation time are in the database (STO-3,
STO-4 still apply).

## 14. Storage

STO2-1. The database is accessed through the Django ORM with migrations. The entity/kind tables of 3.0.0 are replaced by
the models below. Upserts use the ORM, not MySQL syntax (replaces STO-1).

STO2-2. **FeedIndex**: a cache of the parsed feeds built from the workspace on each load: `id` (the feed key of FD2-2,
primary key), `suffix` (unique), `path`, `title`, `tasks`, `tags`, `enabled`, `status` (`ok` / `error`), `error`,
parsed definition (JSON), file modification time. It is not a source of truth and can be rebuilt from disk at any
time.

STO2-2a. Wherever a model below has a `feed_id` field, it holds the feed key of FD2-2: the suffix, or the `id` of a
feed that has one. The field must be long enough for a suffix (255 characters).

STO2-3. **FeedOutput**: `feed_id`, `content_type`, `generated_at`, `item_count`. The document itself is the file of
OUT-6.

STO2-4. **HistoryKey**: `namespace` (SHA-256 of the history key: a feed key or any string, UC-8), `key_hash`,
`seen_at`. Indexed on (`namespace`, `key_hash`) and (`namespace`, `seen_at`). Pruned according to FD2-11. Replaces the
history files of STO-3.

STO2-6. **TaskRun** (`id`, `task`, `trigger` (schedule, manual, then), `started_at`, `finished_at`, `status`) and
**FeedRun** (`task_run`, `feed_id`, `started_at`, `finished_at`, `status`, `headline_count`, `http_status`, `error`).
Pruned after a configurable number of days.

STO2-7. **ScheduleState**: schedule key (task, cron, timezone), `last_fired_at`.

STO2-8. **Subscription**: `feed_id`, `topic`, `callback`, `secret`, `lease_seconds`, `expires_at`, `verified`,
`created_at`. Unique on (`feed_id`, `callback`), so a feed can have several subscribers (changes WS-2).

STO2-9. **WordFilter** (`name`) and **WordFilterEntry** (`filter`, `kind` (word / regex), `value`). WF-1 and WF-2 still
apply.

STO2-10. **KVObject**: `namespace`, `key`, `value` (JSON), `updated_at`, optional `expires_at`. Backs `store_object`,
`fetch_object`, `delete_object`. Values must be JSON-serializable.

STO2-11. **Blob**: `id` (UUID), `sha256`, `content_type`, `size`, `source_url`, `created_at`. Files are stored under
`data/files/blob/<sha256>`, so identical content is stored once. Replaces the image, blob, and text entities. STO-4 to
STO-6 still apply.

STO2-12. **LogEntry**: autoincrement `id` (the sequence number of LOG-2, replacing the counter entity), `level`,
`source`, `timestamp`, `message`, optional `task_run`. Indexed on `timestamp`, `level`, and `source`.

STO2-13. **Settings**: a single row with `enable_profiling`, `subscription_url`. `user_email` is dropped (§17.16).

STO2-14. **ApiToken**: the token is stored hashed. The console shows it once when generated (changes AUTH-6 and
API-16).

STO2-15. Backup (replaces STO-9 and STO-10) is a zip with one JSON file per model for word filters, KV objects,
subscriptions, settings, and schedule state, and optionally a tarball of the workspace. Feed definitions and code are in
the workspace, not the database. Restore upserts the records and, if the archive has a workspace, offers to unpack it
into an empty workspace directory.

STO2-16. MNT-1 still applies and also prunes history (FD2-11) and run records.

## 15. Authentication

AUTH2-1. Authentication uses Django's auth and session framework with one user. Passwords use Django's default hasher.
The bcrypt+sha512 hashes of 3.0.0 are not imported. The password is set again with a management command during
migration (replaces AUTH-1 and the `password-hash` key).

AUTH2-2. AUTH-2 to AUTH-5 still apply. Session cookies are `HttpOnly`, `SameSite=Strict`, and `Secure` when served over
HTTPS. Sessions are stored in the database and survive restarts.

AUTH2-3. All mutating console operations use POST, PUT, or DELETE and are CSRF-protected (fixes §17.10).

AUTH2-4. `/api/images/save` requires the API token (fixes §17.9).

## 16. HTTP interface

API2-1. The console API (§12.1) is replaced by a JSON API under `/front/api/`. The new routes cover: the feed tree,
feed files (read, write, create, move, delete), feed test, suggest selectors, rename suffix (FD2-3b), workspace files
(read, write, create,
rename, delete, tree), load status and errors, tasks (list, run, list feeds), schedules (list with next fire times, edit,
run now), run history, log, settings, token, backup, restore, purge, restart.

API2-2. The public routes keep their paths and behavior: `/feed/<suffix>`, `/feed/uuid:<id>`, `/image/<id>`,
`/handler/<name>`, `/redirect/...`, `/redirect-b64/...`, `/forward/...`, `/forward-b64/...`, `/websub`,
`/backend/run/<task>`, `/backend/reschedule`, `/backend/service-task`, `/api/wordfilter/*`, `/api/images/save`.
`/feed/uuid:<id>` answers only for feeds that have an `id` (FD2-2a), and gives 404 otherwise.

API2-3. A feed URL of a disabled or failed feed behaves as UC2-8.

API2-4. `/image/<id>` must meet IMG-2. When the server runs behind nginx (`x-accel` configuration key), the response is
an `X-Accel-Redirect` to an internal location, and nginx handles ranges, ETags, and streaming. Without it, the Django
view implements IMG-2 itself.

API2-5. WebSub fixes: topics of the form `/feed/uuid:<id>` resolve for feeds that have an `id` (§17.5);
`hub.mode=publish` works (§17.6);
verification appends parameters with `?` or `&` as needed and checks the echoed challenge (§17.7); leases expire
(STO2-8); `GET /websub` answers with 405 (§17.8).

## 17. Console

UI2-1. The console is rebuilt in Vue 3 with the layout, tabs, and behavior of §16 of `REQUIREMENTS.md`, except as
changed here.

UI2-2. Tabs: Workspace, Schedules, Runs, Log, Settings, API (D-13). The Tasks, Library, Extractors, Handlers, and
Scratch tabs of UI-1 are removed, because their files are edited in the workspace tree.

UI2-3. The editor is Ace (D-5), upgraded from 1.4.6 to the current 1.x release, wrapped in a Vue component. Modes:
YAML, Python, Hy, XML, JSON. Theme: Monokai (UI-4).

UI2-4. Hy highlighting uses a custom Ace mode derived from the Clojure mode, with Hy's special forms, the prelude
macros, and `#` reader syntax.

UI2-5. YAML files are validated against the JSON Schema of FD2-4 and the schemas of `tasks.yaml` and `schedules.yaml`.
Errors are shown as Ace annotations on their lines after each save and, if practical, while typing. Schema-driven
autocompletion through `ace-linters` is desirable but not required.

UI2-6. The Workspace tab shows the whole workspace as one tree (`lib/`, `feeds/`, `handlers/`, `tasks.yaml`,
`schedules.yaml`, `scratch.*`) next to an editor with one tab per open file. The tree supports creating files and
folders, renaming, moving by drag and drop, and deleting with confirmation. Tree state persists in local storage (UI-5).

UI2-7. Feed directories are shown as feeds, with the feed title and icon, and feeds that are disabled or failed to load
are marked. Selecting a feed opens its `feed.yaml`. Its code files appear as children. The feed context menu has New
code file, Test, Open feed URL, Subscribe, Run feed task, Suggest selectors, Rename suffix, Rename folder, and Delete.
Test, Suggest selectors, and Rename suffix are also toolbar buttons when a `feed.yaml` is active (UI-8).

UI2-7c. Rename suffix (FD2-3b) asks for the new suffix and says whether the stored state will move. It refuses to run
while the feed's `feed.yaml` has unsaved changes in the editor. Afterwards it reloads the file in the editor and
reports what was moved.

UI2-7a. The toolbar adapts to the active file: Save for every file, Test for `feed.yaml`, Run for the scratch file
(UI-4 output behavior). A load error for the active file is shown in the output pane.

UI2-7b. Python and Hy files have the outline of UI-13 (functions, extractors, handlers, task functions). `tasks.yaml`
has an outline of its tasks plus the task names taken from feeds (UI-14). Right-clicking a task offers Run and List
feeds.

UI2-8. The Schedules tab shows a table of schedules: task, cron expression, its description in words, timezone, next
five fire times, last firing, last run status, enabled toggle, and "Run now". Editing a row changes `schedules.yaml`
while preserving its comments (ruamel.yaml round-trip). A raw editor for the file is also available.

UI2-9. The Runs tab shows recent task runs with status and duration, expandable to their feed runs (headline count,
HTTP status, error). Feeds that returned zero headlines are highlighted unless `expect_empty` is set.

UI2-10. When a file is changed on disk while it is open with unsaved changes in the console, the console warns before
saving over it.

UI2-11. Saving a file reports the result of the reload it triggered: success, or the errors of the files involved.

## 18. Configuration and deployment

CFG2-1. TLS and HTTP/2 are left to a reverse proxy (nginx in the current deployment). The keys `ssl-port`, `keystore`,
`keystore-password`, `keystore-type`, `keystore-reload-interval`, and `sni-host-check` are removed, along with CFG-4 and
CFG-5. `http` is removed because plain HTTP is the only listener.

CFG2-2. `nrepl-port` is replaced by an optional local Python/Hy REPL attached to the worker's namespace, bound to
127.0.0.1.

CFG2-3. `database-connection-url` becomes a Django database URL (`sqlite:///data/feedxcavator.db` by default).
`exclusive-jdbc-connection` and `auto-backup-database` are removed.

CFG2-4. New keys: `workspace`, `x-accel`, `worker-parallel`, `worker-sequential`, `browser-pool-size`,
`browser-headless`, `run-history-days`, and the `service-task` mapping: `enabled` (replaces
`execute-daily-service-task`, which is still read when `enabled` is absent), `time` (MNT-2, `04:00` by default), and
`max-age-days` (the MNT-1 age threshold, 2 by default). The `telegram` mapping holds `api-id` and `api-hash`
(replacing `tg-api-id` and `tg-api-hash`, which are still read when absent) and `default-proxy` (TG2-1).

CFG2-5. CFG-3 environment overrides are kept for the keys that remain.

## 19. Migration from 3.0.0

MIG-1. A management command imports a 3.0.0 `backup.edn` (read with `edn_format`):

- each feed definition becomes `workspace/feeds/<group>/<suffix>/feed.yaml`, with `task`, `realtime`, `partition`,
  `charset`, `proxy`, and `timeout` rewritten into the new fields,
- by default no `id` is written and the feed is identified by its suffix, so `/feed/<suffix>` URLs keep working. With
  `--keep-ids` the old UUID is written as `id`, so `/feed/uuid:<id>` URLs keep working too,
- a feed with no suffix, or with a suffix that is not valid under FD2-3a, is given a valid one and reported, because
  its URL changes,
- subscriptions are imported under the feed key: the suffix, or the UUID with `--keep-ids`,
- the code modules are written to `workspace/legacy/<module>.clj` for manual porting and are not loaded,
- word filters, KV objects, subscriptions, and settings are imported into their models.

MIG-2. The command reports every definition that uses native Enlive selectors (FD-12) or `output: edn`, since these need
rewriting.

MIG-3. The 3.0.0 `schedule` declarations in the `tasks` module are converted to `schedules.yaml` entries, and `deftask*`
declarations to `tasks.yaml` entries, where they can be parsed.

## 20. Resolutions of `REQUIREMENTS.md` §17

| §17 | Resolution                                                                                          |
|-----|-----------------------------------------------------------------------------------------------------|
| 1   | Fixed. User code loads at startup (UC2-6).                                                          |
| 2   | Fixed. A feed with tasks is always served from stored output, with or without `extractor`.          |
| 3   | Fixed (SEL-8).                                                                                      |
| 4   | Fixed (FD2-8, `charset: auto`).                                                                     |
| 5–8 | Fixed (API2-5).                                                                                     |
| 9   | Fixed (AUTH2-4).                                                                                    |
| 10  | Fixed (AUTH2-3).                                                                                    |
| 11  | Fixed. `redirect_to` returns a 302.                                                                 |
| 12  | Fixed (SEL-6).                                                                                      |
| 13  | Fixed. `html_unescape` uses Python's `html.unescape`.                                               |
| 14  | Fixed. The top-of-log query returns `n` entries.                                                    |
| 15  | Fixed (TSK2-3).                                                                                     |
| 16  | Fixed (STO2-13).                                                                                    |
| 17  | Moot. `/` redirects to `/console`.                                                                  |

## 21. Research

R-1. **Stealth browser.** Evaluate Camoufox, Patchright, nodriver/zendriver, SeleniumBase UC/CDP mode, and Scrapling's
fetchers against CreepJS, bot.sannysoft, BrowserScan, the fingerprint.com demo, a Cloudflare Turnstile page, and the
sources of the existing browser-based extractors. Compare pass rate, memory use, startup time, headful and headless
behavior on Linux and Windows, and maintenance activity. Also measure how many currently blocked sources curl_cffi alone
unblocks. Output: the engine for BRW-1 and a default profile configuration.

R-2. **Automatic extraction.** Evaluate the libraries of AUTO-4 on a set of 20 to 30 listing pages taken from the
existing feeds, scoring item detection and field accuracy against the hand-written selectors. Output: the libraries for
AUTO-1 steps 1 to 3 and for AUTO-3, and whether step 3 needs a custom heuristic.

R-3. **Scheduler and queue.** Compare APScheduler in the worker with django-q2 and Huey (SQLite backend) on cron
support, misfire handling, behavior on SQLite, and how they fit two worker pools and TSK2-5. Output: the engine for
SCH2-6 and PRC-3.

R-4. **Telegram client.** Compare TDLib through a Python binding (pytdbot, aiotdlib) with MTProto clients that need no
native library (Telethon, Pyrogram forks). Criteria: maintenance, async support, media download, proxy support, whether
the 3.0.0 session in `data/tddb` can be reused, and packaging on Windows and Linux. Output: the library for TG-1 to TG-7
and whether the client stays running in the worker.

## 22. Open questions

None at the moment.