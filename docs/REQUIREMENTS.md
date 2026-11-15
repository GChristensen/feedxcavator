# Feedxcavator requirements

Feedxcavator is a self-hosted, single-user server that turns web pages and other data sources into feeds (RSS, JSON
Feed, plain JSON, EDN) and serves them over HTTP. Feeds are described in YAML. Anything the YAML cannot express is
written as code in the browser console and loaded into the running server. Slow feeds are extracted in the background by
scheduled tasks, stored, and pushed to feed readers through a built-in WebSub hub.

This document lists what a reimplementation on another stack has to do to behave like version 3.0.0 (Clojure, Ring/Jetty
12, H2, ClojureScript with Google Closure). Requirements carry an ID so they can be referenced from tickets. "Must"
marks behavior that clients, stored data, or user code depend on. "Should" marks behavior worth keeping that can change
without breaking them.

Section 17 lists places where the current code does something other than what its documentation says. Decide on each
before porting.

## 1. Scope and terms

In scope: the server, the web console, the feed definition format, the user code model (extractors, tasks, schedules,
handlers), storage, and deployment.

Out of scope: the static demo (`demo/`, a copy of the console wired to a mock backend), the landing page (`landing/`),
the unfinished browser extension (`app/extension/`), and the personal scripts in `tools/`.

| Term            | Meaning                                                                                                           |
|-----------------|-------------------------------------------------------------------------------------------------------------------|
| Feed definition | The YAML text the user edits. Stored verbatim.                                                                    |
| Feed            | The parsed form of a definition, one record per definition, addressed by UUID.                                    |
| Headline        | One feed item: a map with `title`, `link`, `summary`, and optional fields.                                        |
| Extractor       | A function that takes a feed and returns headlines. Either the built-in selector-based one or a user-defined one. |
| Task            | A named group of feeds extracted together in the background. The name comes from the `task` field of the feeds.   |
| Supertask       | A task defined in code as a list of other tasks, with optional follow-up tasks.                                   |
| Schedule        | A rule that enqueues a task at a local time of day.                                                               |
| Handler         | A user-defined HTTP endpoint under `/handler/`.                                                                   |
| Word filter     | A named set of strings and regular expressions used to drop headlines.                                            |

## 2. Runtime and configuration

CFG-1. The server must run as one process that reads `config.yml`, `data/`, and `log/` relative to its working
directory.

CFG-2. The server must read these keys from `config.yml`. A missing file is treated as an empty configuration.

| Key                                      | Default        | Purpose                                                                                        |
|------------------------------------------|----------------|------------------------------------------------------------------------------------------------|
| `port`                                   | 10000          | Plain HTTP port.                                                                               |
| `host`                                   | all interfaces | Bind address for both listeners.                                                               |
| `http`                                   | true           | `false` turns off the plain HTTP listener.                                                     |
| `ssl-port`                               | none           | HTTPS port. HTTPS is on only when both `ssl-port` and `keystore` are set.                      |
| `keystore`                               | none           | Path to the keystore with the certificate and private key.                                     |
| `keystore-password`                      | none           | Keystore password.                                                                             |
| `keystore-type`                          | `PKCS12`       | Keystore format.                                                                               |
| `keystore-reload-interval`               | none           | Seconds between checks for a changed keystore file.                                            |
| `sni-host-check`                         | true           | `false` disables the SNI host check.                                                           |
| `stacktraces`                            | true           | `false` stops the server from returning stack traces in error pages.                           |
| `nrepl-port`                             | none           | Starts a REPL bound to 127.0.0.1 on this port.                                                 |
| `hostname`                               | none           | Base URL used in generated links when no request is in progress.                               |
| `websub-hostname`                        | none           | Base URL used in feed URLs and the hub URL.                                                    |
| `secure-hostname`                        | none           | Base URL used for forward links requested with `https`.                                        |
| `authentication`                         | true           | `false` disables the login requirement.                                                        |
| `user`                                   | none           | Login name of the single user.                                                                 |
| `password-hash`                          | none           | Password hash in the buddy-hashers format, `bcrypt+sha512$<salt>$<cost>$<hash>`.               |
| `websub`                                 | true           | `false` disables the hub.                                                                      |
| `database-connection-url`                | none           | JDBC URL. The shipped value is an embedded H2 file in MySQL mode.                              |
| `exclusive-jdbc-connection`              | false          | Use one shared connection for all statements.                                                  |
| `auto-backup-database`                   | false          | Copy the H2 database file to `<file>.bakN` at every start, where N is the first unused number. |
| `execute-daily-service-task`             | false          | Run the maintenance task (MNT-1) every day at 04:00 local time.                                |
| `production`                             | false          | Return a bare 500 page from `/feed/` on errors.                                                |
| `tdlib-path`, `tg-api-id`, `tg-api-hash` | none           | Telegram client settings (section 14).                                                         |

CFG-3. These environment variables must override the matching keys: `FEEDXCAVATOR_PORT`, `FEEDXCAVATOR_HOST`,
`FEEDXCAVATOR_SSL_PORT`, `FEEDXCAVATOR_KEYSTORE_PASSWORD`, `FEEDXCAVATOR_NREPL_PORT`.

CFG-4. If `http` is false and HTTPS is not configured, the server must print an error to stderr and exit with code 1.

CFG-5. The HTTPS listener must negotiate HTTP/2 through ALPN and fall back to HTTP/1.1. When `keystore-reload-interval`
is set, a replaced keystore must be picked up without a restart.

CFG-6. The server must accept percent-encoded slashes (`%2F`) and other encoded characters inside a path segment. The
redirect routes pass a whole URL as one segment.

CFG-7. A stalled client must not block other requests. Media players often read the start of a streamed file and then
stop reading while keeping the connection open. The current server runs requests on virtual threads and closes idle
connections after 60 seconds.

CFG-8. The server must not send a `Server` version header.

CFG-9. A `--dev` command-line flag should enable code reloading for development.

CFG-10. At startup the server prints one line with the active listeners, for example
`Feedxcavator started on http *:10000, https+h2 *:11000`.

## 3. Feed definition

### 3.1 Storage and parsing

FD-1. A feed definition is YAML text. The server must store the text exactly as the user saved it, comments included,
and return it unchanged when the editor opens the feed.

FD-2. On save, the server must also parse the text into a feed record. The fields in the table below are stored as typed
columns. Every other top-level key goes into a single map of extra fields and must be available to extractors as an
ordinary feed field with the same name.

FD-3. Each feed has a UUID assigned at creation: 32 lowercase hex characters without hyphens. The UUID is the key of
both the definition text and the parsed record.

FD-4. A YAML syntax error on save must return HTTP 500 with the parser message as the body. Nothing is stored in that
case.

### 3.2 Fields

| Field       | Type    | Default | Meaning                                                                                                   |
|-------------|---------|---------|-----------------------------------------------------------------------------------------------------------|
| `title`     | string  | none    | Feed title. Shown in the console tree and in the output.                                                  |
| `suffix`    | string  | none    | The feed is served at `/feed/<suffix>`. Must be unique, but the server does not check this.               |
| `source`    | string  | none    | URL of the source page. Also the base for relative links and for page paths.                              |
| `charset`   | string  | `utf-8` | Charset used to decode the source.                                                                        |
| `output`    | string  | `rss`   | One of `rss`, `json-feed`, `json`, `edn`.                                                                 |
| `group`     | string  | none    | Slash-separated path of the feed in the console tree, for example `news/local`.                           |
| `task`      | string  | none    | Name of the background task that extracts this feed. A feed without a task is extracted on every request. |
| `parallel`  | boolean | true    | `false` puts the feed on the sequential worker (TSK-6).                                                   |
| `proxy`     | string  | none    | Name of a proxy defined in user code. Used for every fetch made while extracting this feed.               |
| `timeout`   | integer | 0       | Connect and read timeout for the source request, in seconds. 0 means no timeout.                          |
| `selectors` | map     | none    | CSS selectors, section 3.3.                                                                               |
| `pages`     | map     | none    | Pagination, section 3.4.                                                                                  |
| `filter`    | map     | none    | Filtering, section 3.5.                                                                                   |
| `realtime`  | boolean | false   | Publish the feed through WebSub after each background extraction.                                         |
| `partition` | integer | none    | With `realtime`, push the feed in parts of this many items.                                               |
| `extractor` | string  | none    | Name of a user-defined extractor. Without it the built-in extractor is used.                              |
| `params`    | any     | none    | Arbitrary data for the extractor.                                                                         |

FD-6. "New Feed" in the console must create a feed from this template. Only the first three lines are active.

```yaml
title: '*Untitled feed'
suffix: untitled-feed
source: https://example.com
#charset: utf-8
#output: rss
#parallel: true
#group: group/subgroup
#task: task-name
#proxy: default
#timeout: 0
#selectors:
#  item: div.headline
#  title: h3
#  link: a
#  summary: div.preview
#  image: div.cover img:first-of-type
#  author: div.user-name
#  category: div.category
#pages:
#  include-source: true
#  path: '/?page=%n'
#  increment: 1
#  start: 2
#  end: 2
#  delay: 0
#filter:
#  history: true
#  content: title+summary
#  wordfilter: default
#realtime: true
#partition: 100
#extractor: extractor-function-name
#params: [any addiitional data, [123, 456]]
```

### 3.3 Selectors

FD-7. `selectors` maps a part name to a selector. `item` selects the container element of each headline. The other parts
(`title`, `link`, `summary`, `content`, `image`, `author`) are evaluated inside each item, and the first match is used.

FD-8. The value of a part is the inner HTML of the matched element. `link` and `image` are different: by default they
take the `href` and `src` attributes.

FD-9. A selector can name the attributes to read after a double colon, with alternatives separated by a double pipe:
`div.cover img::src-lazy||src`. The value is the first of those attributes present on the element.

FD-10. `:root` as a part selector refers to the item element itself.

FD-11. The supported CSS subset is:

| Feature                                            | Example                                                       |
|----------------------------------------------------|---------------------------------------------------------------|
| Element, ID, class                                 | `div`, `div#id`, `div.cls`                                    |
| Descendant                                         | `h1 a`                                                        |
| Child                                              | `div#id > h1.cls > a`                                         |
| Attribute present                                  | `a[attr]`                                                     |
| Attribute equals, starts with, contains, ends with | `a[attr="v"]`, `a[attr^="v"]`, `a[attr*="v"]`, `a[attr$="v"]` |
| Pseudo-class without arguments                     | `h1:first-child`                                              |
| Pseudo-class with one numeric argument             | `a:nth-of-type(3)`                                            |
| Alternatives                                       | `h2, h3`                                                      |

FD-12. A selector that starts with `[`, `{`, or `#{` is passed to the Enlive library as a native selector without
conversion. A port has to choose between dropping this syntax and offering an equivalent escape hatch. Existing
definitions that use it need rewriting either way.

FD-13. Relative `link` and `image` values must be made absolute against `source`:

- `//host/path` gets the `http:` scheme.
- `/path` is appended to the scheme and host of `source`.
- `./path` and bare `path` are resolved against the directory of `source`.
- Anything containing `://` is left alone.

FD-14. An item in which every extracted part is blank must be dropped.

FD-15. Each headline from the built-in extraction also carries `html`, the parsed item element, so user code can extract
more from it. `html` is never serialized.

### 3.4 Pages

FD-16. `pages` makes the extractor read several pages and concatenate their headlines in order.

| Key              | Default | Meaning                                                               |
|------------------|---------|-----------------------------------------------------------------------|
| `include-source` | true    | Read the `source` URL first.                                          |
| `path`           | none    | Template appended to `source`. `%n` is replaced with the page number. |
| `start`          | 2       | First page number.                                                    |
| `end`            | none    | Last page number, before multiplication by `increment`.               |
| `increment`      | 1       | Step between page numbers.                                            |
| `delay`          | none    | Seconds to wait before each page request.                             |

FD-17. The page numbers are `start`, `start + increment`, and so on, up to and including `end × increment`. With
`increment: 20`, `start: 20`, `end: 3` the numbers are 20, 40, 60.

### 3.5 Filter

FD-18. `filter.history: true` drops headlines whose `link` appeared in the previous extraction of the same feed. After
filtering, the stored history is replaced with the links of the current extraction before filtering. The history
therefore holds only the last batch.

FD-19. `filter.content` is `title` or `title+summary`. A headline is dropped when one of the named fields matches the
word filter. `filter.wordfilter` names the filter and defaults to `default`.

FD-20. The history filter runs before the content filter. A test run from the console (API-6) must skip the history
filter so that testing does not consume history.

## 4. Extraction and output

### 4.1 Extraction flow

EX-1. To extract a feed, the server merges the extra fields into the feed (FD-2), then calls the extractor named in
`extractor`, or the built-in extractor when the field is blank.

EX-2. The built-in extractor fetches `source` (and the pages, if any), applies the selectors, filters the headlines, and
serializes them.

EX-3. When the source cannot be fetched, the built-in extractor must return a feed with one item titled `Error`. The
item summary is `Network error.` for a connection failure, `HTTP error: <status>` for a non-2xx response, and
`Probably feed selectors are out of sync with the source markup.` otherwise.

EX-4. A user-defined extractor behaves according to the feed and the caller:

| Feed has `task` | Caller          | Behavior                                                                                                             |
|-----------------|-----------------|----------------------------------------------------------------------------------------------------------------------|
| no              | feed URL        | Run the extractor body, filter, serialize, return. Return nothing if there are no headlines.                         |
| yes             | background task | Run the body and filter. If any headlines remain: serialize, store the output, publish (WS-6). Return the headlines. |
| yes             | feed URL        | Return the stored output without running the body.                                                                   |
| any             | console test    | Run the body, apply the content filter only, serialize, return. Nothing is stored or published.                      |

EX-5. While an extractor runs, the current feed must be available as ambient context. Fetch calls made inside take their
default `charset` and `proxy` from it.

EX-6. A feed that has a `task` but no `extractor` is run by tasks through a built-in background extractor. That
extractor follows EX-4 with the selector-based parser as its body.

### 4.2 Headline fields

EX-7. Extractors return a list of maps with these keys. All are optional, but a headline with neither a title nor a link
is of little use to a reader.

| Key          | Meaning                                                                             |
|--------------|-------------------------------------------------------------------------------------|
| `title`      | Headline title.                                                                     |
| `link`       | Headline URL. Also the history key.                                                 |
| `summary`    | HTML body.                                                                          |
| `content`    | Alternative full content.                                                           |
| `author`     | Author name.                                                                        |
| `category`   | A single category.                                                                  |
| `image`      | Image URL.                                                                          |
| `image-type` | MIME type of the image. Guessed from the file extension when absent.                |
| `guid`       | Stable identifier. Used by `filter-history-by-guid!` and by the RSS `guid` element. |

### 4.3 Output formats

EX-8. `rss` output must be RSS 2.0 with content type `application/rss+xml`:

- The root element declares `xmlns:atom` and sets `xml:base` to the feed `source`.
- The channel has `title`, `link` (the source), `atom:link rel="self"` with the feed URL, `pubDate`, and
  `lastBuildDate`. Both dates are the generation time in RFC 822 form.
- A `realtime` feed also has `atom:link rel="hub"` pointing at `<websub-hostname>/websub`.
- Each item has `title`, `link`, `pubDate`, `description` (the summary), and `content`. `author` and `category` appear
  when set.
- `guid isPermaLink="false"` holds the headline `guid`. For a `realtime` feed without a `guid`, it holds the link.
- An item with an image has `enclosure` with `url` and `type`.
- Item dates decrease by 5 seconds per item, starting 6 seconds before the generation time, so readers that sort by date
  keep the source order.
- All text values are HTML-escaped (`<`, `>`, `"`, `'`, `&`).

EX-9. `json-feed` output must be JSON Feed version 1 with content type `application/json`. It has `version`, `title`,
`home_page_url`, `feed_url`, and `items`. A `realtime` feed adds `hubs` with type `WebSub`. Each item has `id` and `url`
(both the link), and `title`, `content_html` (the summary), `image`, and `author.name` when set.

EX-10. `json` and `edn` output must be the list of headline maps with all their fields except `guid` and `html`. Content
types are `application/json` and `application/edn`. These formats exist so another program, or another feed, can
aggregate the result.

### 4.4 Serving feeds

EX-11. `GET /feed/<suffix>` returns the feed whose `suffix` matches. `GET /feed/uuid:<uuid>` addresses a feed by UUID.
An unknown feed gives 404. An extraction that returns nothing gives 204. Responses carry `Cache-Control: no-cache`.

EX-12. The public URL of a feed is `<websub-hostname>/feed/<suffix>`. A feed without a suffix uses
`<websub-hostname>/feed/uuid:<uuid>`.

## 5. Fetching

FT-1. One fetch function serves both the built-in extractor and user code. It takes a URL and these options:

| Option             | Meaning                                                                                                                                                                                                                       |
|--------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `method`           | HTTP method. Default GET.                                                                                                                                                                                                     |
| `headers`          | Request headers.                                                                                                                                                                                                              |
| `payload`          | Request body, string or bytes.                                                                                                                                                                                                |
| `as`               | Response conversion: `html` (parsed HTML tree), `xml` (parsed XML tree), `json` (parsed, keys as keywords), `string`, `bytes`. Without it the raw response is returned with two extra fields, `final-url` and `content-type`. |
| `charset`          | Charset for decoding. Defaults to the current feed's charset, then `utf-8`.                                                                                                                                                   |
| `timeout`          | Connect and read timeout in milliseconds.                                                                                                                                                                                     |
| `proxy`            | Name of a defined proxy. Defaults to the current feed's proxy. `false` forces a direct connection.                                                                                                                            |
| `follow-redirects` | Default true.                                                                                                                                                                                                                 |
| `retry`            | `false` for one attempt, or a number of retries. Default is two retries (three attempts).                                                                                                                                     |
| `async?`           | Fire the request without waiting. Async requests are never retried.                                                                                                                                                           |
| `insecure?`        | Skip TLS certificate checks.                                                                                                                                                                                                  |

FT-2. The function must never throw on network or HTTP errors. It returns nothing and records the failure in per-request
state, readable through `get-last-http-response`, `get-last-http-error` (status code), `get-last-network-error`
(exception), and `get-last-conversion-error`. The state is reset at the start of every fetch and must be isolated per
HTTP request and per background job.

FT-3. A response with status below 300 is a success.

FT-4. Retries apply to exceptions and to statuses 408, 425, 429, 500, 502, 503, and 504. The delay before attempt n+1 is
based on 100 ms × 2^ (n−1) with equal jitter: half the backoff plus a random part of the other half.

FT-5. A `Retry-After` header (seconds or HTTP date) must be honored. The wait is never shorter than requested and is
extended by up to 10% so clients do not all return at once. If the server asks for more than 10 seconds, the fetch gives
up without retrying.

FT-6. A `log-fetch-errors` wrapper must make every failed fetch inside it write an error entry with the URL and the
status or exception message to the application log.

FT-7. User code defines proxies by name with `host`, `port`, `user`, and `password`. Proxy definitions live in memory
and are rebuilt when user code loads.

## 6. User code

The current server compiles Clojure source typed into the console into a dedicated namespace of the running process. A
port has to provide an embedded scripting facility with the same capabilities. Code already written by users will need
porting unless the new stack can run Clojure.

UC-1. User code is stored as five named modules: `library`, `tasks`, `extractors`, `handlers`, and `scratch`. Each is
one text blob with a modification timestamp.

UC-2. A full load must clear all registered tasks, schedules (cancelling their timers), and handlers, then evaluate
`library`, `tasks`, `extractors`, and `handlers` in that order. All four share one global scope, so later modules see
definitions from earlier ones.

UC-3. Saving a module other than `scratch` must evaluate that module first and report any error to the console with its
full text. If evaluation succeeds, the server performs a full load and responds `Successfully saved.`

UC-4. Saving `scratch` evaluates only the scratch code and returns everything it printed to standard output. Scratch
code sees all definitions from the other modules.

UC-5. A full load must also happen once after the server starts. The current server defers it to the first HTTP request
(see section 17).

UC-6. User code must be able to register the following.

| Construct                                         | Purpose                                                                         |
|---------------------------------------------------|---------------------------------------------------------------------------------|
| `defextractor name [feed] body`                   | An extractor, referenced by name from the `extractor` field. Semantics in EX-4. |
| `defhandler name [params] body`                   | An HTTP handler (section 9).                                                    |
| `deftask* name [subtasks] :on-completion [tasks]` | A supertask (TSK-3).                                                            |
| `schedule`, `schedule-weekly`, `schedule-monthly` | Schedules (section 8).                                                          |
| `defproxy name :host :port :user :password`       | A proxy (FT-7).                                                                 |

UC-7. User code must have access to this library, described in full on the console's API tab:

- Fetching: `fetch-url`, the last-error accessors, `log-fetch-errors`.
- HTML: select all matches, select first, attributes of first, children of first, text content, outer HTML, string to
  tree, HTML unescape.
- Extraction: `apply-selectors`, `parse-html-page`, `parse-pages`, `parse-page-range`, `produce-feed-output`.
- Filtering: `filter-history` (read only), `filter-history!`, `filter-history-by-guid!`, `filter-content`,
  `filter-headlines`, and add/remove for filter words and regexes.
- URLs: `fix-relative-url`, `redirect-url`, `redirect-url-b64`, `forward-url`, `forward-url-b64`, `get-feed-url`,
  `get-app-host`, URL encoding, Base64 and URL-safe Base64.
- Utilities: `md5`, HMAC-SHA1 signing, UUID and random hex generation, millisecond timestamp, `distinct-by`, regex
  escaping, pretty printing.
- Responses: builders for text, HTML, JSON, RSS, attachment, redirect, 403, 404, and 500 replies.
- Storage: `store-object!`, `fetch-object`, `delete-object!` (arbitrary values by string key), `find-feed` by suffix or
  title, `persist-feed!`, `fetch-feed-output`, `store-feed-output!`.
- Logging: `log/write` (LOG-1).
- Browser automation and Telegram (section 14).

UC-8. The history functions accept a feed or any string as the key, so one extractor can keep separate histories, for
example one per forum URL.

UC-9. If user code defines a list named `profiling-ignore`, feeds whose suffix contains one of its strings are left out
of the health check in TSK-9.

## 7. Tasks and supertasks

TSK-1. Every distinct non-empty `task` value among the feeds is a task. It needs no declaration. Running it extracts all
feeds whose `task` equals the name, compared case-insensitively.

TSK-2. A task name that matches no feed and no supertask still resolves to a task with no feeds. Running it does nothing
and reports success.

TSK-3. A supertask is declared in code with a name, a list of subtask names, and an optional list of on-completion task
names:

```clojure
(deftask* morning-feeds [news articles papers]
          :on-completion [aggregate-feeds remind-me])
```

TSK-4. Running a supertask starts all of its subtasks at once. Each subtask name is resolved directly to feeds as in
TSK-1. A supertask listed as a subtask of another is not expanded.

TSK-5. When every feed job of every subtask has finished, the server enqueues each on-completion task in order.
On-completion names go through the normal task lookup, so they can be supertasks. This is the mechanism for chaining
stages, for example extracting sources first and then running an aggregating feed that reads their stored output.

TSK-6. Feed jobs run on two process-wide worker pools shared by all tasks: a parallel pool of 6 workers, and a
sequential pool of 1 worker for feeds with `parallel: false`. Enqueuing a task submits one job per feed and returns
immediately.

TSK-7. A feed job calls the feed's extractor in background mode (EX-4) under a logging source equal to the extractor
name. An exception in a job is written to the log as an error and must not affect other jobs. The job reports the feed
UUID, the number of headlines returned, and the elapsed time.

TSK-8. `GET /backend/run/<task>` enqueues a task and responds `<task> enqueued` as plain text, or `ERROR` if enqueuing
failed. The route requires a logged-in session.

TSK-9. When the `enable-profiling` setting is on, the server writes one log entry per finished task under the source
`profiling`. It contains the task name, the total time, and for each feed its title, time (`N/D` if unknown), and
headline count. The entry level is `warning` if any feed returned zero headlines (subject to UC-9), otherwise `info`. A
finished supertask also gets an entry with its name and total time.

TSK-10. The console must list the feed titles of a task on request (`/front/list-task-feeds`). For a supertask this is
the union of its subtasks' feeds.

TSK-11. Tasks do not survive a restart. A task that was running when the server stopped is not resumed.

## 8. Scheduling

SCH-1. Schedules are declared in user code, by convention in the `tasks` module:

```clojure
(schedule news 12 00)                 ; every day at 12:00
(schedule-weekly digest 8 30 1)       ; Mondays at 08:30, days are 1 (Monday) to 7
(schedule-monthly report 6 0 15)      ; the 15th of each month at 06:00
```

SCH-2. All times are in the server's local time zone.

SCH-3. A schedule fires once a day at the given hour and minute. Weekly and monthly schedules also fire daily and
enqueue the task only when the current day of week or day of month matches.

SCH-4. If the time of day has already passed when the schedule is created, the first run is the next day. Missed runs
are not made up after downtime.

SCH-5. A task can have any number of schedules. A second declaration with the same task, time, and day is ignored.

SCH-6. A full load of user code (UC-2) must cancel every existing timer before the new declarations create theirs, so
that removed schedules stop firing and none fire twice.

SCH-7. `GET /backend/reschedule` cancels all timers and recreates them from the current declarations. It requires a
session.

SCH-8. Firing a schedule enqueues the task exactly as TSK-8 does. The schedule does not wait for the task to finish, and
overlapping runs of one task are not prevented.

SCH-9. `schedule-periodically task hours` is accepted and does nothing. The console outline still shows it. Implement it
or remove it.

## 9. Handlers

HND-1. A handler registered under `name` answers every HTTP method on `/handler/<name>`. An unknown name gives 404.

HND-2. A handler declared with a parameter list receives the request parameters (query string or form) of the same names
as arguments, in order. A handler declared with the single word `request` receives the whole request.

HND-3. The handler's return value is the HTTP response, built with the reply helpers of UC-7.

HND-4. A handler marked `^:auth` must return 403 unless the request header `x-feedxcavator-auth` equals the stored API
token (AUTH-6).

HND-5. A handler runs under a logging source equal to its name.

## 10. WebSub hub

WS-1. `POST /websub` is the hub endpoint. It reads form parameters and dispatches on `hub.mode`. The whole hub is
inactive when the `websub` config key is false.

WS-2. On `subscribe`, the hub finds the feed from the suffix in `hub.topic` and stores one subscription per feed, keyed
by feed UUID: feed title, topic, callback URL, secret, and timestamp. A new subscriber for the same feed replaces the
previous one. If the stored topic and callback already match, nothing is done.

WS-3. After storing, the hub sends a verification GET to the callback with `hub.mode`, `hub.topic`, `hub.lease_seconds`,
and a random `hub.challenge`, and responds 202. It does not check the echoed challenge. Leases never expire.

WS-4. On `unsubscribe`, the hub deletes the subscription, sends the same verification GET without waiting for the
result, and responds 202.

WS-5. Calls to subscriber callbacks must bypass any proxy and must not verify TLS certificates. Subscribers are usually
readers on the same private network.

WS-6. After a background extraction stores output for a `realtime` feed, the server POSTs it to the subscriber's
callback:

- `Content-Type` is the output content type with `; charset=utf-8`.
- `Link` is `<feed-url>; rel="self", <hub-url>; rel="hub"`.
- If the subscription has a secret, `X-Hub-Signature` is `sha1=` followed by the hex HMAC-SHA1 of the body.
- If the callback URL contains `feedly.com`, `&hub.mode=publish` is appended to it.

WS-7. If the feed has `partition: N`, the headlines are split into parts of N items. The parts are sent as separate
complete feed documents, last part first, with a one-second pause before each. Readers that cap the number of items per
push then receive everything in source order.

WS-8. A failed delivery is logged as an error with the status, the response body, and the response. The error is logged
under the source `websub` and must not fail the feed job.

WS-9. Deleting a feed deletes its subscription.

## 11. Authentication and access control

AUTH-1. There is one user, defined by `user` and `password-hash` in the configuration. Passwords are checked against a
bcrypt+sha512 hash.

AUTH-2. `GET /login` serves a form with `username` and `password` fields. `POST /login` checks them. On success it
stores the identity in a server-side session and redirects to the `next` query parameter, or to `/`. On failure it shows
the form again.

AUTH-3. The session cookie must be `HttpOnly` and `SameSite=Strict`. Sessions are held in memory and are lost on
restart.

AUTH-4. `GET /logout` clears the session and redirects to `/login`.

AUTH-5. With authentication on, an unauthenticated request is redirected to `/login?next=<path>` unless its path is `/`,
`/login`, or starts with one of: `/api/`, `/image/`, `/feed/`, `/handler/`, `/redirect/`, `/redirect-b64/`, `/forward/`,
`/forward-b64/`, `/websub`. Everything else needs a session, including `/console`, `/front/*`, and `/backend/*`.

AUTH-6. A single API token (40 random hex characters) is stored in the database. It is created on first request and can
be regenerated from the Settings tab. The word-filter API and `^:auth` handlers require it in the `x-feedxcavator-auth`
header and return 403 without it.

AUTH-7. Responses carry `X-Frame-Options: SAMEORIGIN`. The current server has no CSRF protection.

## 12. HTTP interface

### 12.1 Console API

The console talks to `/front/*`. GET parameters are query parameters. POST bodies are `multipart/form-data`. Responses
are plain text unless noted. Where the table says EDN, the current server prints Clojure data. A port can use JSON as
long as the console is changed to match.

| ID     | Method and path                | Parameters           | Result                                                                                           |
|--------|--------------------------------|----------------------|--------------------------------------------------------------------------------------------------|
| API-1  | GET `/front/list-feeds`        | none                 | EDN list of `{uuid, group, title}` sorted by title.                                              |
| API-2  | GET `/front/create-new-feed`   | none                 | Creates a feed from the template (FD-6). Returns the UUID.                                       |
| API-3  | GET `/front/feed-definition`   | `uuid`               | The YAML text (FD-5).                                                                            |
| API-4  | POST `/front/feed-definition`  | `uuid`, `yaml`       | Saves. `Successfully saved.`, or 500 with the error message.                                     |
| API-5  | GET `/front/delete-feed`       | `uuid`               | Deletes the feed, its definition, and its subscription. `OK`.                                    |
| API-6  | POST `/front/test-feed`        | `uuid`, `yaml`       | Runs the unsaved definition in test mode (EX-4) and returns the output. RSS is pretty-printed.   |
| API-7  | GET `/front/feed-url`          | `uuid`               | The public feed URL (EX-12).                                                                     |
| API-8  | GET `/front/run-feed-task`     | `uuid`               | Enqueues the feed's task. `OK`.                                                                  |
| API-9  | GET `/front/get-tasks`         | none                 | EDN list of distinct task names taken from the feeds.                                            |
| API-10 | GET `/front/list-task-feeds`   | `task`               | Feed titles, one per line (TSK-10).                                                              |
| API-11 | GET `/front/get-code`          | `type`               | Source of a code module.                                                                         |
| API-12 | POST `/front/save-code`        | `type`, `code`       | Stores and loads the module (UC-3, UC-4). Returns the message or the scratch output.             |
| API-13 | GET `/front/get-log-entries`   | `n`, optional `from` | EDN list of log entries, newest first (LOG-3).                                                   |
| API-14 | GET `/front/clear-log`         | none                 | Deletes all entries and resets the counter. `OK`.                                                |
| API-15 | GET `/front/server-log`        | none                 | Contents of `log/app.log`.                                                                       |
| API-16 | GET `/front/get-auth-token`    | none                 | The API token. Creates one if absent.                                                            |
| API-17 | GET `/front/gen-auth-token`    | none                 | Generates and returns a new token.                                                               |
| API-18 | GET `/front/get-settings`      | none                 | EDN map: `enable-profiling`, `subscription-url`, `user-email`, plus `version` and `restartable`. |
| API-19 | POST `/front/save-settings`    | `settings` (EDN map) | Stores the settings.                                                                             |
| API-20 | GET `/front/backup-database`   | none                 | Downloads `backup.edn` (STO-9).                                                                  |
| API-21 | POST `/front/restore-database` | `edn` (file)         | Imports a backup (STO-10). `OK`.                                                                 |
| API-22 | GET `/front/purge-images`      | none                 | Deletes every stored image. `OK`.                                                                |
| API-23 | POST `/front/restart-server`   | none                 | Restarts (LFC-2). `OK`, or 500 with an explanation when unsupervised.                            |

### 12.2 Public and utility routes

| ID     | Method and path                               | Behavior                                                                                                                       |
|--------|-----------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------|
| API-24 | GET `/`                                       | Redirects to `/console`.                                                                                                       |
| API-25 | GET `/console`                                | The console page.                                                                                                              |
| API-26 | GET `/feed/<suffix>`                          | Serves a feed (EX-11).                                                                                                         |
| API-27 | ANY `/image/<uuid>`                           | Serves a stored image (IMG-2).                                                                                                 |
| API-28 | POST `/api/images/save`                       | Stores the raw request body as an image, using the request `Content-Type`. Returns the new UUID as text. No token is required. |
| API-29 | ANY `/handler/<name>`                         | Runs a handler (section 9).                                                                                                    |
| API-30 | ANY `/redirect/<random>/<url>`                | 302 to the URL-decoded `<url>`. The random segment only makes the link unique.                                                 |
| API-31 | ANY `/redirect-b64/<random>/<url>`            | 302 to the URL-safe Base64-decoded `<url>`.                                                                                    |
| API-32 | GET `/forward/<random>/?url=&referer=&proxy=` | Fetches `url` with the given `Referer` (and proxy, if named) and returns the upstream headers and body with status 200.        |
| API-33 | GET `/forward-b64/<data>?proxy=`              | Same. `<data>` is URL-safe Base64 of `<url>;;<referer>`.                                                                       |
| API-34 | POST `/websub`                                | The hub (section 10).                                                                                                          |
| API-35 | GET `/backend/run/<task>`                     | Enqueues a task (TSK-8).                                                                                                       |
| API-36 | GET `/backend/reschedule`                     | Recreates schedule timers (SCH-7).                                                                                             |
| API-37 | GET `/backend/service-task`                   | Starts the maintenance task (MNT-1) in the background.                                                                         |

API-38. Any other path returns 404 with a short HTML body. Static console assets are served from the application's
bundled resources.

API-39. `redirect-url` and `forward-url` (UC-7) build links to routes API-30 to API-33. Extractors use them to hide a
target from a reader, or to serve media that requires a specific referer or proxy.

### 12.3 Word filter API

All four routes are POST with a JSON body and require the API token (AUTH-6).

| ID     | Path                           | Body                                           | Result                                                       |
|--------|--------------------------------|------------------------------------------------|--------------------------------------------------------------|
| API-40 | `/api/wordfilter/add-regex`    | `{"word-filter": "<name>", "regex": "<expr>"}` | 204. 404 if `regex` is missing.                              |
| API-41 | `/api/wordfilter/remove-regex` | same                                           | 204.                                                         |
| API-42 | `/api/wordfilter/list-words`   | `{"word-filter": "<name>"}`                    | JSON array of entries. 404 if the filter is empty or absent. |
| API-43 | `/api/wordfilter/list`         | none                                           | JSON array of filter names.                                  |

`word-filter` defaults to `default` in all of them.

WF-1. A word filter is a set whose entries are plain strings or regular expressions. A plain string is stored in lower
case and matches when the lower-cased field contains it. A regular expression is compiled case-insensitive and
Unicode-aware and matches anywhere in the lower-cased field.

WF-2. Listing a filter returns regex entries as their source text without the flags prefix.

## 13. Storage

STO-1. The server needs a relational database for records and a directory for large payloads. The current schema is
`app/src/sql/tables.sql`. The current code upserts with MySQL-style `INSERT ... ON DUPLICATE KEY UPDATE`.

STO-2. The logical entities are:

| Entity          | Key                  | Content                                                                                                                              |
|-----------------|----------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| feed-definition | uuid                 | The YAML text.                                                                                                                       |
| feed            | uuid                 | Parsed fields (section 3.2), with `selectors`, `pages`, `filter`, `params`, and the extra-field map stored as serialized structures. |
| code            | type                 | Module source and timestamp.                                                                                                         |
| settings        | kind (`main`)        | `enable-profiling`, `subscription-url`, `user-email`.                                                                                |
| auth-token      | kind (`main`)        | The API token.                                                                                                                       |
| subscription    | uuid (feed)          | `name`, `topic`, `callback`, `secret`, `timestamp`.                                                                                  |
| word-filter     | id (name)            | The set of entries.                                                                                                                  |
| object          | uuid (any string)    | An arbitrary serialized value stored by user code.                                                                                   |
| image           | uuid                 | Source `url`, `content-type`, `timestamp`. Indexed on `url` and `timestamp`.                                                         |
| blob, text      | uuid                 | `content-type` of a payload file.                                                                                                    |
| log             | kind (`main`)        | Number of the newest entry.                                                                                                          |
| log-entry       | uuid                 | `number`, `level`, `source`, `timestamp`.                                                                                            |
| log-message     | uuid (same as entry) | The message text.                                                                                                                    |

STO-3. Payloads live in files under `data/files/`, named by their key:

- `feed/<feed-uuid>` holds the stored output of a background feed. Its content type is in the matching `text` record.
- `history/<sha256 of the history key>` holds the history set (FD-18).
- `image/<image-uuid>` holds image bytes. The content type is in the matching `blob` record.

STO-4. Payload files must be written to a temporary file in the same directory and moved into place atomically, so a
concurrent reader never sees a partial file. The temporary name is the target name plus `.<32 hex>.tmp`.

STO-5. An image file must be written before its database records, and the records must be written in one transaction. An
image visible in the database then always has its data.

STO-6. Path components built from keys must be sanitized: every character other than letters, digits, `-`, `_`, `.`,
`/`, and `\` becomes `_`.

STO-7. Database access must be safe from concurrent request threads and background workers. The current server uses a
pool of 16 connections for embedded H2.

STO-8. At startup the server must create the two image indexes if they do not exist.

STO-9. A backup is one downloadable file containing all records of these entities: feeds, feed definitions,
subscriptions, auth tokens, objects, word filters, code, and settings. Regular expressions in word filters are exported
as strings that begin with `(?iu)`.

STO-10. Restore upserts every record from a backup. Feed records are rebuilt by parsing the definitions again, not
copied from the backup. Word filter entries that begin with `(?iu)` become regular expressions again. Images, stored
feed output, history, and log entries are in neither backup nor restore.

### Images

IMG-1. Images are stored by API-28 and by the Telegram integration. Each gets a UUID and is addressed as
`<app-host>/image/<uuid>`.

IMG-2. `/image/<uuid>` must stream the file from disk without loading it into memory and must support:

- `Content-Type` from the stored record, defaulting to `application/octet-stream`.
- `Content-Length`, `Accept-Ranges: bytes`, `Last-Modified`.
- `ETag` built from the file's modification time and length.
- `Cache-Control: public, max-age=31536000, immutable`.
- A single byte range (`bytes=a-b`, `bytes=a-`, `bytes=-n`) answered with 206 and `Content-Range`. An unsatisfiable
  range gets 416 with `Content-Range: bytes */<length>`. A multi-range or malformed header gets the whole file.
- `If-Range` with either the ETag or the `Last-Modified` value. If it does not match, the whole file is sent.

Video stored through the same route relies on range support for seeking.

### Maintenance

MNT-1. The maintenance task deletes images and log entries older than 48 hours. A failure to delete one image is logged
and does not stop the task. When done it writes an info entry under the source `service-task` with the elapsed time and
the counts.

MNT-2. The task runs daily at 04:00 local time when `execute-daily-service-task` is true, and on demand through API-37.

## 14. Optional integrations

These are libraries exposed to user code. A port can ship without them if no existing extractor uses them.

INT-1. Browser automation. User code configures a WebDriver browser once (`webdriver/config :firefox {...}`, creating
the profile directory if one is given) and runs code inside `with-browser`, which starts a driver session and closes it
afterwards. `webdriver/apply-selectors` applies the feed's CSS selectors to the live page and returns headlines as FD-7
to FD-14 describe, with text content where the built-in extractor gives inner HTML. Driver binaries such as geckodriver
are looked up in the installation directory.

INT-2. Headless client. A scriptable HTTP client with optional JavaScript execution, a JS timeout (default 30 s), and a
host blocklist for scripts. A helper waits out a Cloudflare browser check by enabling JavaScript and pausing (default 7
s).

INT-3. Telegram. `telegram/get-channel-messages <channel>` returns headlines for the most recent messages of a public
channel (up to 26), using TDLib with a session database in `data/tddb`. It does nothing if that directory is absent.

- Messages that share an album ID become one headline.
- The summary is the message text rendered from Markdown to HTML. Bare URLs are made into links, and a leading `#` on a
  word is replaced with `_`.
- Photos and video thumbnails are saved to the image store and appended as `<img>` tags. A file already stored under the
  same path is reused.
- The link is the message permalink.
- Service messages (pins, title and photo changes) are skipped.
- Calls are serialized. Each call starts the client, optionally routes it through the proxy named `default`, and shuts
  it down afterwards.

`telegram/notify <message> <chat-id>` sends a text message.

## 15. Logging and process lifecycle

### Application log

LOG-1. `log/write [level] message` appends an entry. `level` is `info` (default), `warning`, or `error`. A string
message is stored as is, an exception as its stack trace, and any other value pretty-printed.

LOG-2. Each entry has a UUID, a sequence number that increases by one per entry, a level, a timestamp in milliseconds,
and the logging source active when it was written (TSK-7, HND-5, or none). Writes must be serialized so numbers are
unique.

LOG-3. Reading takes a count `n` and an optional `from` number and returns entries newest first, each with its message.
Without `from` it returns the newest entries. With `from` it returns entries numbered from `from` down to
`from − n + 1`.

LOG-4. An uncaught exception on any thread must be written to stderr and to the application log as an error.

### Server log

LOG-5. Process-level logging goes to `log/app.log`, rotated at 5 MB with one backup. The console can display this file
(API-15).

### Lifecycle

LFC-1. Launcher scripts for Windows and Linux, and a systemd user unit, start the server from the installation directory
with that directory first on `PATH`. They set `FEEDXCAVATOR_SUPERVISED=1` and start the server again whenever it exits
with code 3.

LFC-2. A restart request from the console is honored only when `FEEDXCAVATOR_SUPERVISED=1`. The server logs the request,
responds, and exits with code 3 half a second later so the response is delivered. `restartable` in the settings (API-18)
tells the console whether to enable the button.

LFC-3. On normal shutdown the server must stop the listeners and terminate all child processes it started, such as
browser drivers and browsers.

LFC-4. While running, the server must record its live descendant processes (PID and start time) in `data/children.pid`
every 10 seconds, rewriting the file only when the set changes. At the next start it must kill any recorded process that
is still alive with the same PID and start time, then delete the file. This cleans up after a forced kill, which skips
shutdown hooks.

LFC-5. At start the server should delete temporary payload files (STO-4) older than the process itself.

## 16. Web console

The console is a single page at `/console`. It needs no build step at run time and keeps no state on the server apart
from what the API stores.

### Layout

UI-1. The header shows the logo and product name. Below it a tab bar has nine tabs: Feeds, Tasks, Library, Extractors,
Handlers, Scratch, Log, Settings, API. A tab's content is built the first time it is shown.

UI-2. The URL fragment selects the initial tab, for example `#log`, `#settings`, `#tasks-code`. Without a fragment the
Feeds tab opens.

UI-3. On load the console fetches the settings and the task list and keeps both for later use.

UI-4. The console uses a dark theme. Code editors need syntax highlighting for YAML, Clojure (or the port's scripting
language), and XML, soft tabs of two spaces, and undo history.

### Feeds tab

UI-5. The left side is a tree of feeds. Feeds are grouped into folders by their `group` path, with missing intermediate
folders created automatically. Entries are sorted by name, case-insensitively. Expansion and selection state persists in
the browser's local storage.

UI-6. The "New Feed" button above the tree creates a feed (API-2), reloads the tree, and selects the new feed.

UI-7. Selecting a feed loads its YAML into the editor on the right. If the editor has unsaved changes, the console asks
"Discard unsaved changes?" first.

UI-8. The editor toolbar has these buttons:

- Save saves the definition (API-4), shows the result in the output pane for two seconds, then reloads the tree and the
  task list. Ctrl+S or Cmd+S in the editor does the same, and the browser's own save dialog is suppressed.
- Test runs the unsaved text (API-6) and shows the resulting feed in the output pane. JSON output is pretty-printed. A
  server error is shown as readable text extracted from the error page.
- Open opens the feed URL in a new browser tab.
- Subscribe opens the "Subscription URL" setting with the encoded feed URL appended. The fallback is
  `https://newsblur/?url=`.
- Delete deletes the feed after a confirmation.

UI-9. Right-clicking a feed opens a menu with "Open feed URL", "Subscribe", "Run feed task", and "Delete".

UI-10. Below the editor are a status bar with a spinner that shows while a request is running, and a read-only output
pane.

### Code tabs

UI-11. Tasks, Library, Extractors, Handlers, and Scratch share one layout: an outline on the left, a code editor on the
right with one toolbar button ("Save", or "Run" on Scratch), and an output pane below. Ctrl+S saves or runs.

UI-12. Saving shows the server's reply in the output pane. On the four non-scratch tabs a success message clears after
two seconds. Errors and scratch output stay.

UI-13. The outline lists top-level definitions found by scanning the source text: tasks, extractors, handlers,
functions, and schedules. Each kind has its own icon. A schedule is shown as `task (hour:min)`. The outline refreshes
three seconds after the last edit. Clicking an entry moves the cursor to its line.

UI-14. On the Tasks tab the outline also lists every task name taken from the feeds (API-9). Right-clicking a task
offers "Run" (API-35) and "List feeds", which prints the feed titles in the output pane.

### Log tab

UI-15. The toolbar has Refresh, Clear (with confirmation), and Server Log. The list shows the newest 20 entries and
loads 10 more whenever the user scrolls to the bottom.

UI-16. Each entry shows a level icon, the timestamp in browser local time, the source if any, and the message in a
collapsed block. Clicking the entry header or `[...]` expands the message. `[wrap]` toggles line wrapping.

UI-17. Server Log replaces the list with the contents of `log/app.log`, scrolled to the end.

### Settings tab

UI-18. The tab has these groups:

- Customization holds the subscription URL and user e-mail. Both are saved when the field loses focus.
- Authentication shows the API token and a Generate button.
- Database has a file picker with a Load button for restore, a backup download link, and a Purge button for stored
  images with a confirmation and a status text.
- Server has a Restart button. When the server is not supervised the button is disabled and a text explains why. After a
  confirmed restart the console polls `/login` once a second and reloads the page when the server answers.
- Logging has the "Enable profiling" checkbox, saved on change.

The application version is shown at the bottom.

### API tab

UI-19. A read-only reference of everything available to user code (UC-6, UC-7). It must be kept in step with the actual
library.

### Login page

UI-20. A standalone page with username and password fields that posts to `/login`.

## 17. Behavior to decide on before porting

Each item is a place where the current code and its documentation disagree, or where the code looks unfinished.
Reproduce or fix them deliberately.

1. Schedules start late. User code is first loaded on the first HTTP request after start, so no schedule fires until
   someone or something calls the server. A port should load user code at startup.
2. Task feeds without an extractor are extracted live. When a feed has `task` but no `extractor`, a request to its feed
   URL runs the built-in extractor against the source and does not return the stored output. With `filter.history` on,
   that request also consumes history. The README says such feeds are served from storage.
3. The `category` selector has no effect. The built-in extractor never reads it, although the template and README list
   it. `category` works only when a user-defined extractor sets it. When present, the RSS output writes the `category`
   element twice.
4. The response charset is ignored. Decoding always uses the feed's `charset`, or UTF-8. The code that reads `charset=`
   from the response `Content-Type` passes its arguments in the wrong order and never finds the header.
5. WebSub subscriptions by UUID do not work. A topic of the form `/feed/uuid:<uuid>` is not resolved to a feed.
6. WebSub `hub.mode=publish` is broken. It passes the stored output to the publisher in the wrong shape.
7. WebSub verification assumes the callback URL already has a query string. It appends parameters with `&`, and it
   accepts any reply.
8. `GET /websub` falls through to 404. Only POST is handled.
9. `/api/images/save` is unauthenticated. The token check is commented out.
10. Mutating console operations use GET (delete feed, clear log, purge images, generate token), and there is no CSRF
    protection.
11. `api/redirect-to` in the user library returns a plain text page where a redirect is expected.
12. `fix-relative-url` gives protocol-relative URLs the `http:` scheme even when the source is HTTPS.
13. `html-unescape` turns `&quot;` into `&`.
14. The top-of-log query returns `n + 1` entries.
15. Supertasks do not nest (TSK-4), and `deftask*` is the only way to declare one.
16. `user-email` in the settings is stored and never used. It is left over from a removed mail feature.
17. The home route computes an authentication-dependent redirect and then discards it. The observable result is always a
    redirect to `/console`.
