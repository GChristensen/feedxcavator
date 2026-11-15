# Feedxcavator 4

Feedxcavator is a self-hosted, single-user server that converts web pages and other data sources into feeds
(RSS, Atom, JSON Feed or plain JSON) and serves them. Simple feeds are described in YAML. Anything YAML cannot
express is written as Python or [Hy](https://hylang.org/) code next to the feed, so the extracted content can be
transformed in any way: sorted, filtered, merged with other sources, enriched from linked pages. Slow feeds are
extracted in the background on a schedule, and a built-in [WebSub](https://en.wikipedia.org/wiki/WebSub) hub
pushes their updates to feed readers as soon as they are ready.

Feed definitions and code live as plain files in a workspace directory, which can be edited in the web console,
in an IDE, or kept under git. The server is written in Python with Django, and the console in Vue.

## Semi-automatic feed extraction

Feedxcavator converts web pages to feeds with CSS selectors. Let's create a feed that takes several pages of a
site and converts them to RSS. Create the file `workspace/feeds/my-first-feed/feed.yaml` (or use **New feed** in
the console) with the following text:

```yaml
title: My first feed
source: https://example.com
selectors:
  item: article
  title: h2::text
  link: h2 a
  image: img::attr(data-src)||src
pages:
  include-source: false
  path: '/page/{n}'
  start: 1
  end: 5
```

The feed is available at `http://<feedxcavator host>:10000/feed/my-first-feed`: the directory name is its
default suffix. Every time this URL is visited, Feedxcavator reads the pages from https://example.com/page/1 to
https://example.com/page/5, finds every element matched by the `item` selector, and builds a headline from each
of them with the other selectors, which are applied inside the item.

A link is taken from the `href` attribute of the matched element and an image from its `src` attribute, unless
the selector names an attribute. The definition above takes the image from `data-src`, and from `src` when
`data-src` is absent. Relative links are made absolute against the source URL.

The console can test a feed without publishing it, and **Suggest selectors** in the feed context menu proposes a
`selectors` block for the source page.

## Automatic extraction

A feed with `mode: auto` and no selectors lets Feedxcavator work out the headlines by itself. It looks, in this
order, for a feed the site already publishes, for structured data on the page (JSON-LD and microdata), and for a
repeated structure of elements that looks like a list of articles. OpenGraph data is used last, because it
describes the page itself rather than a list.

```yaml
title: Lazy feed
source: https://example.com/blog
mode: auto
fulltext: true   # replace each summary with the main text of the linked article
```

## Feed definition reference

Every key is optional except `source` (or an `extractor` that does not need one). The file the console creates
for a new feed lists them all as comments.

```yaml
title: Feed title
suffix: feed-suffix            # the feed is served at /feed/feed-suffix; defaults to the directory name
id: 0123456789abcdef0123456789abcdef  # keeps the stored state when the suffix changes (see "Feed identity")
enabled: true
tags: [news]                   # tasks can select feeds by tag
extends: forums/phpbb          # inherit from forums/phpbb.yaml in a group lib/ or in lib/; maps are merged
source: https://example.com    # a URL or a list of URLs
mode: selectors                # or auto
fetch:
  backend: http                # or browser, for pages that need JavaScript
  impersonate: chrome          # the browser whose TLS fingerprint and headers are imitated
  method: GET
  headers: {Accept-Language: en}
  cookies: {name: value}
  charset: auto                # set it when the automatic detection fails
  proxy: default               # the name of a proxy from proxies in config.yml
  timeout: 30                  # seconds
  retry: 2
  wait_for: article            # browser backend: a CSS selector to wait for
  scroll: 0                    # browser backend: scroll to the bottom this many times
selectors:
  item: div.headline           # the container element of a headline
  title: h3
  link: a
  summary: div.preview
  content: div.body
  image: div.cover img:first-of-type
  author: div.user-name
  category: {css: 'div.category a::text', many: true}
  published: {css: 'time::attr(datetime)', parse: date}
  guid: '::attr(data-id)'
pages:                         # numbered pages
  include-source: true         # also read the URL in `source`
  path: '/?page={n}'           # appended to the source URL
  start: 2
  end: 3
  increment: 1
  delay: 0                     # seconds between page requests
#pages:                        # or follow a "next" link
#  next: 'a.next::attr(href)'
#  max: 5
filter:                        # applied in this order
  - history: {key: link, keep: 1000}  # omit headlines seen the previous time
  - dedupe: link
  - wordfilter: {name: default, fields: [title, summary]}
  - exclude: {field: title, regex: '^Sponsored'}
  - limit: 50
fulltext: false                # true: fetch each linked page and use its main text as the summary
transform: function-name       # a function applied to every headline
output: rss                    # rss, atom, json-feed or json
tasks: [task-name]             # extract in the background (see "Extracting feeds in the background")
parallel: true                 # false: run on the sequential worker pool
websub: {enabled: true, partition: 100}  # push updates, by parts of 100 items
expect_empty: false            # true: zero headlines is normal, no profiling warning or highlight in run history
extractor: extractor-name      # the code that produces the headlines instead of the selectors
params: [any additional data, [123, 456]]  # arbitrary data for the code
```

Any other top-level key is kept and available to code as a feed field.

## Selectors

Selectors are full CSS as implemented by [parsel](https://github.com/scrapy/parsel) and cssselect: any combinator, attribute operator, `:not()`, `:nth-child()` and so on can be used. Feedxcavator adds a few things at the end of a selector:

| Selector | Result |
|---|---|
| `h2` | the inner HTML of the element (for `link` and `image`, its `href` or `src`) |
| `h2::text` | the text of the element, without tags |
| `img::attr(data-src)` | the value of an attribute |
| `img::attr(data-src)\|\|src` | the first non-empty attribute of the list (`img::data-src\|\|src` also works) |
| `:root::attr(data-id)` | the item element itself, written as `:root` |
| `xpath://h2/a/@href` | an XPath expression |
| `jmes:data.items[*]` | a [JMESPath](https://jmespath.org/) expression for JSON sources |

When the `item` selector is a `jmes:` expression, the source is parsed as JSON, so a JSON API can be turned into a
feed without code. XML sources (another RSS feed, for example) are parsed with namespaces removed.

A selector can also be a map with options:

```yaml
selectors:
  item: li.post
  title: {css: 'a::text', strip: true}
  category: {css: '.tags a::text', many: true}         # a list of all matches
  published: {css: '.date::text', parse: date}         # almost any date format
  comments: {css: '.comments::text', parse: int}
  summary: {xpath: './/p[1]', parse: text}
  author: {css: '.byline::text', regex: 'by (\w+)', default: unknown}
```

Exactly one of `css`, `xpath` or `jmes` is required. `regex` keeps the first group of the match, or the whole
match when there are no groups. Selectors are checked when the file is saved, and errors are shown with their line
numbers.

## Python and Hy

Code files sit in the workspace next to the definitions: shared code in `lib/`, feed-specific code in the feed
directory, HTTP handlers in `handlers/`. A `.py` file is Python and a `.hy` file is Hy, a Lisp that compiles to
Python. The Hy prelude adds a compact DSL for feed code: definition macros such as `defextractor` and
`defhandler`, keyword lookup with `(:title h)`, threading macros, and Clojure-style collection functions (`assoc`,
`update-in`, `group-by` and others) that never modify their arguments. Python is there for those who would rather
not write Lisp; the collection functions are available in it too.

Both languages can be mixed freely, even in one feed directory, because all files evaluate into one shared
namespace. Hy names are translated into Python names by the usual Hy rules, so a function defined in Hy as
`parse-forum` is called from Python as `parse_forum`, and a Python function `clean_title` is `clean-title` in Hy.
Every Python library installed in the environment can be imported from either language.

The user library (fetching, selecting, filtering, storage, responses) is available in every file without
imports. The **API** tab of the console lists it with signatures in both spellings. A few names that Hy
mangling cannot produce have Hy aliases: `filter-history!` is `filter_history_update` in Python,
`store-object!` is `store_object`, `str->html` is `parse_html`.

Some differences to keep in mind:

- In Python, the `as` and `async` options are passed as `as_` and `async_`: `fetch_url(url, as_="html")`.
  In Hy they are written `:as "html"`.
- Hy reads `:key value` in a call as a keyword argument, so option values are written as strings:
  `(fetch-url url :as "html")`. For the same reason `(distinct-by "link" coll)` takes the key as a string.
- `(:key m)` returns None for a missing key. A feed is a read-only mapping: `feed["source"]`, `feed.source` and
  `(:source feed)` are the same. Hy mangles dashes in keywords, so `(:image-type h)` reads the `"image_type"`
  key; read a key that contains a dash with `(get h "item-id")`.
- Headlines are plain dicts with string keys, and `assoc` and the other collection functions take the keys as
  strings: `(assoc h "title" "x")`. `(assoc h :title "x")` does not work, because `:title` in a call is a
  keyword argument.

## Custom feed extractors

Without the `extractor` field, Feedxcavator extracts the feed with the selectors. Otherwise the field names an
extractor function defined in code. A feed directory with exactly one extractor uses it even without the field.

An extractor receives the feed and returns a list of headlines, dicts with these fields:

```python
{
    "title": "Headline title",
    "link": "Headline URL",
    "summary": "Article summary (optional)",
    "content": "Full article content (optional)",
    "author": "Author name (optional)",
    "category": "A category or a list of categories (optional)",
    "image": "Image URL (optional)",
    "image_type": "image/mime-type (optional)",
    "published": "a datetime (optional)",
    "guid": "a unique id (optional)",
}
```

RSS, Atom and JSON Feed output ignore other fields; plain JSON output keeps all of them. Here is an extractor
that sorts the headlines found by the feed selectors by title, in both languages:

```python
@extractor("my-first-extractor")
def my_first_extractor(feed):
    headlines = parse_html_page(feed, feed.source)
    return sorted(headlines or [], key=lambda h: h["title"])
```

```hy
(defextractor my-first-extractor [feed]
  (sorted (or (parse-html-page feed (:source feed)) [])
          :key (fn [h] (:title h))))
```

To change each headline rather than the whole list, a `transform` function is simpler. It receives one headline
and returns a changed copy, None to keep it as it is, or False to drop it:

```python
def shout(headline):
    return {**headline, "title": headline["title"].upper()}
```

## Fetching web resources

`fetch_url` (`fetch-url` in Hy) obtains the contents of a web resource. It does not raise on network or HTTP
errors; it returns None instead. `get_last_http_error` returns the status code of the last failed request,
`get_last_http_response` the whole response, and `get_last_network_error` the network exception. Inside a
`log_fetch_errors` block (the `log-fetch-errors` macro in Hy) every failed fetch is written to the log, which
helps when debugging.

The `as` option converts the response:

- `html`: a parsed HTML document for the selector functions
- `xml`: a parsed XML document
- `json`: parsed JSON
- `string`: the response text
- `bytes`: the response body

Without it the raw response is returned. Other options include `method`, `headers`, `payload`, `cookies`,
`proxy`, `retry`, `impersonate` and `timeout`, which is in milliseconds here (while `fetch.timeout` in
`feed.yaml` is in seconds). Requests go through curl_cffi and imitate a real browser: `impersonate` names the
curl_cffi fingerprint (`chrome` by default, also `firefox`, `safari` and versioned names such as `chrome124`).

### Proxies

Proxies are defined by name under `proxies` in `config.yml`, either as a URL or as a mapping:

```yaml
proxies:
  default: socks5://127.0.0.1:1080
  work:
    scheme: http          # http (the default), https, socks5, socks5h or socks4
    host: proxy.example.com
    port: 3128
    user: me              # optional
    password: secret      # optional
```

In the URL form, special characters in the user name and password must be percent-encoded. Changes take effect
after a restart.

A fetch uses the proxy named by its `proxy` option, otherwise the one named by `fetch.proxy` of the current
feed. `proxy=False` forces a direct connection. A name that is not defined means a direct connection, without
an error.

The name `default` is not applied to feeds by itself; a feed must name it in `fetch.proxy`. Telegram calls,
however, always go through the proxy named `default` when one is defined. The `/forward` links made by
`forward_url(url, referer, proxy="name")` fetch through the named proxy. WebSub callbacks never use a proxy.

### Browsers

Pages that need JavaScript can be opened in a real browser, either with `fetch.backend: browser` in the feed or
from code:

```python
with browser() as b:
    page = b.open(feed.source, wait_for="article", scroll=3)
    headlines = page.apply_selectors(feed)
```

```hy
(with-browser [b]
  (-> (.open b (:source feed) :wait-for "article")
      (.apply-selectors feed)))
```

The browser is set up in `config.yml`:

| Key | Default | Meaning |
|---|---|---|
| `browser-engine` | `patchright` | `patchright`, `camoufox` (Firefox based) or `playwright` |
| `browser-channel` | `chrome` | the installed browser patchright or playwright drives; the bundled Chromium when it is missing |
| `browser-pool-size` | `1` | how many browsers may run at once; further jobs wait |
| `browser-headless` | see below | `true` or `false`; by default headful on Linux when a display or Xvfb is available, otherwise headless |

Browsers run in the worker; code in the web process (the console) reaches them over the local RPC. Each
`with browser()` block starts a browser and closes it at the end.

A profile is a named, persistent browser data directory, `data/browser/<profile>/`, which keeps cookies, local
storage and logins between runs. Profiles need no definition: a new name creates its directory on first use.
Choose one with `browser(profile="shop")` or `(with-browser [b :profile "shop"] ...)`; the default is
`default`, and `fetch.backend: browser` always uses `default`. One profile can be open in one browser at a
time, so jobs on the same profile wait for each other even when the pool has room. To log in to a site once,
open the profile headful (`browser-headless: false`), log in by hand, and later runs reuse the session.

After a page is opened, later `fetch_url` calls to the same host reuse the browser's cookies and user agent
(kept in memory by the process until it restarts), so a browser can pass a challenge and plain fetches can do
the rest.

The browser does not use the proxies defined above: `fetch.proxy` has no effect on `fetch.backend: browser`.
Engine and channel are global; profiles cannot set their own.

### Telegram

`telegram.get_channel_messages(channel)` returns headlines for the latest messages of a public Telegram channel,
and `telegram.notify(message, chat_id)` sends a text message. Both need `tg-api-id` and `tg-api-hash` in
`config.yml` and a one-time login with `python -m feedxcavator telegram-login`.

## Selecting elements

Fetch a page with `as_="html"` and take its parts with the selector functions, which accept the selectors
described above:

```python
q(node, "div.post")              # all matches: elements, or strings for ::text and ::attr()
q1(node, "div.post h2::text")    # the first match, or None
text(node)                       # text content without tags
attr(node, "href")               # the value of an attribute
inner_html(node)                 # the HTML inside the element
outer_html(node)                 # the HTML of the element itself
extract(node, base_url=url, title="h2::text", link="a")  # one headline, as the YAML selectors build it
apply_selectors(doc, feed)       # all headlines, with the selectors of the feed
```

In Hy, the `$` and `$1` macros do the same as `q` and `q1`, and check a literal selector when the file is loaded.
They also accept a selector written as a vector of keywords, where `:>` is the child combinator:

```hy
($ page "div.post > a")
($1 page [:div.post :> :a])
(defselectors post-parts :item "article" :title "h2::text" :link "h2 a")
(post-parts page)                ; headlines
```

## Extracting feeds in the background

A feed without `tasks` is extracted each time its URL is visited. A feed with `tasks` is extracted in the
background: the result is stored and served when a user or a feed reader visits the URL, and pushed to WebSub
subscribers. This suits feeds that need complex or slow processing.

Tasks are defined in `workspace/tasks.yaml`. A feed belongs to a task when it lists the task in its `tasks`
field, or when the task selects it by tag, by path or by suffix:

```yaml
tasks:
  news:
    feeds: {tags: [news]}
  forums:
    feeds: {path: 'forums/*'}
  papers:
    feeds: {list: [city-paper, county-paper]}
  morning-feeds:
    run: [news, forums]           # subtasks, started together
    then: [aggregate-feeds]       # tasks started when all subtasks have finished
  aggregate-feeds:
    call: aggregate               # a task function defined in code
```

A task may also have `pre` and `post` functions, a `timeout`, and a `concurrency` policy (`skip`, `queue` or
`allow`) for when it is triggered while still running.

Schedules are in `workspace/schedules.yaml` and are managed on the **Schedules** tab. They use cron expressions in
the local time zone unless a `timezone` is given. A task can be scheduled several times:

```yaml
schedules:
  - task: news
    cron: '0 12 * * *'
  - task: news
    cron: '0 17 * * *'
  - task: morning-feeds
    cron: '0 7 * * *'
    timezone: Europe/Berlin
```

A task can be started at any time with **Run now** on the Schedules tab, and the tasks of a feed with **Run feed
task** in its context menu. The **Runs** tab shows what ran, when, and with what result.

## Handlers

Handlers define a custom web API for your feeds. A handler registered with the name `my-first-handler` is called
when `http://<feedxcavator host>:10000/handler/my-first-handler` is visited. Its arguments receive the query or
form parameters of the same names. A handler whose only argument is called `request` receives the whole request
instead (`method`, `path`, `headers`, `body`, `params`, `json`).

With `auth=True` (`^:auth` in Hy) the request must carry the API token in the `x-feedxcavator-auth` header. The
token is generated on the Settings tab. It is stored hashed and shown only once, so copy it when it appears. A handler returns one of the response functions (`text_page`,
`json_page`, `web_page`, `redirect_to` and others), a dict or list that is sent as JSON, or None for an empty
response.

```python
@handler("my-first-handler")
def my_first_handler(param):
    log.write(param)
    return text_page("OK")
```

```hy
(defhandler ^:auth my-secure-handler request
  (setv feed (find-feed "kitty-site"))
  (if (= (:method request) "GET")
      ;; return the stored kitty feed for external processing
      (do (setv output (fetch-feed-output feed))
          (web-page (:content-type output) (:output output)))
      ;; store the externally processed output
      (do (store-feed-output! feed {"content_type" "application/json"
                                    "output" (.decode (:body request) "utf-8")})
          (text-page "OK"))))
```

## Logging

`log.write` writes a string, an exception (with its traceback) or any other value to the **Log** tab. A level
can be given as the first argument: `info` (the default), `warning` or `error`.

```python
log.write("warning", f"no items at {feed.source}")
```

## Word filters

A word filter removes headlines whose fields contain certain words. The `wordfilter` filter step names the
filter and the fields to check; without a name the filter called `default` is used.

Word filters contain plain words, matched case-insensitively anywhere in the field, and regular expressions. A
word boundary `\b` in a regular expression helps to avoid matching parts of longer words. Filters are managed
from code (for example, from `scratch.hy`, which runs from the console) with `add_filter_word`,
`remove_filter_word`, `add_filter_regex`, `remove_filter_regex` and `list_word_filter`, or through the REST API at
`/api/wordfilter/<action>` (`list`, `list-words`, `add-word`, `remove-word`, `add-regex`, `remove-regex`), which
takes a JSON body and the API token. The author uses
[these](https://gist.github.com/GChristensen/c4be3bb8508ad13d982c2f57ac302eb8) commands for the
[iShell browser extension](https://gchristensen.github.io/ishell).

```hy
(add-filter-regex "default" r"\bsponsored\b")
```

## Code examples

Fetch new threads from a set of bulletin board forums. The `params` field of the feed holds the numeric ids of the
forums, and the feed selectors describe the thread list of a forum page.

```hy
(defextractor bb-extractor [feed]
  (lfor forum (:params feed)
        :setv forum-url (+ (:source feed) (str forum))
        ;; stage 1: thread links from the forum page, without the ones seen before
        thread (filter-history! forum-url (or (parse-html-page feed forum-url) []))
        ;; stage 2: the first post of every thread
        :setv page (fetch-url (:link thread) :as "html")
        (do (log.write (+ "visited: " (:link thread)))
            (assoc thread
                   "title" (html-unescape (:title thread))
                   "image" (q1 page ".post_text img::attr(src)")
                   "summary" (outer-html ($1 page ".post_text"))))))
```

Extract data from a JSON API. The `params` field holds the user id.

```python
API_TOKEN = "..."

@extractor("json-api-extractor")
def json_api_extractor(feed):
    url = f"https://json.api/method/data.get?user_id={feed.params}&access_token={API_TOKEN}&v=1"
    posts = get_in(fetch_url(url, as_="json"), ["response", "items"], [])
    headlines = [{"title": p.get("title"),
                  "link": p.get("url"),
                  "summary": p.get("text"),
                  "image": get_in(p, ["attachments", "photo_640"]) or get_in(p, ["attachments", "photo_320"])}
                 for p in posts]
    return sorted(headlines, key=lambda h: h["link"] or "", reverse=True)
```

Transform another RSS feed and turn its titles upper case. The selectors read the XML, and a transform function
changes the titles, so no extractor is needed:

```yaml
title: Loud feed
source: https://example.com/rss.xml
selectors:
  item: item
  title: title::text
  link: link::text
  summary: description::text
transform: shout
```

```hy
(defn shout [headline]
  (assoc headline "title" (.upper (:title headline))))
```

A task function that runs after the morning feeds:

```python
@task_function("aggregate")
def aggregate(ctx):
    log.write(f"aggregating after {ctx['trigger']}")
```

## Installation

Feedxcavator needs Python 3.12 or later.

```
uv venv && uv pip install -e ".[dev]"        # or: python -m venv .venv && pip install -e ".[dev]"
copy config.example.yml config.yml           # edit hostname, user, ...
.venv\Scripts\python -m feedxcavator set-password
feedxcavator.cmd                             # feedxcavator.sh on Linux
```

The launcher starts a supervisor that runs two processes: web (ASGI on uvicorn) and worker (the scheduler, the
task pools, the browser pool and Telegram). It reads `config.yml`, `data/`, `log/` and `workspace/` from the
working directory, or from `FEEDXCAVATOR_HOME` when it is set. The console is at `http://<host>:10000/console`.
The listening port and interface are set with `port` and `host` in `config.yml`, or with the `FEEDXCAVATOR_PORT`
and `FEEDXCAVATOR_HOST` environment variables.

Commands (`python -m feedxcavator <command>`):

| Command | Purpose |
|---|---|
| `run` (default), `--dev` | supervisor: web + worker; `--dev` reloads the web process on code changes |
| `web`, `worker` | one process only |
| `set-password [--user U] [--password P]` | create the user or set its password |
| `telegram-login` | interactive Telegram login, creates `data/telegram/` |
| `manage <django command>` | any Django management command |

When `repl-port` is set (or `FEEDXCAVATOR_REPL_PORT`), the worker opens a Python/Hy REPL bound to 127.0.0.1
that shares the namespace of the workspace code.

The browser backend drives the installed Chrome through patchright. Without Chrome, run
`python -m patchright install chromium`.

The server can be restarted from the Settings tab when it runs under the supervisor or the systemd unit.
Child processes (browsers) are stopped on shutdown. When the server is killed forcibly, the children it left
behind are stopped on the next start.

### HTTPS

Feedxcavator listens on plain HTTP. Please put it behind an HTTPS reverse proxy when it is reachable from the
internet. `deploy/nginx.conf` is a ready configuration for nginx with TLS and HTTP/2; it can also serve stored
images directly when `x-accel` is set in `config.yml`. Set `secure-hostname` to the public HTTPS address.

### Running as a systemd user service

Copy the installation to `~/feedxcavator` and install the bundled unit:

```sh
mkdir -p ~/.config/systemd/user
cp ~/feedxcavator/deploy/feedxcavator.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now feedxcavator
loginctl enable-linger $USER   # keep the service running without an active login session
journalctl --user -u feedxcavator -f
```

On Windows, `feedxcavator.cmd` can be registered as a service with [WinSW](https://github.com/winsw/winsw) or
[NSSM](https://nssm.cc/), with the installation directory as the working directory.

## Workspace

```
workspace/
  lib/                     shared code, loaded first (lib/_order.yaml sets an explicit order)
    forums/phpbb.yaml      a template for `extends: forums/phpbb`
  feeds/news/
    lib/                   group library: code and templates for the feeds under news/
    local/city-paper/
      feed.yaml            the feed; the directory path is its group, the directory name its default suffix
      extract.hy           feed-local code
  handlers/
  tasks.yaml
  schedules.yaml
  scratch.hy               run from the console only
```

Edits made in the console, in an IDE or by git are picked up the same way. A broken feed file disables only that
feed; a broken `lib/` file keeps the previously loaded state running. If a `lib/` file fails at startup, there is
no previous state to keep, so the rest of the workspace loads without that file and the error is reported.

A group folder under `feeds/` may have its own `lib/` directory. Its code loads before the feeds of the group, and
the feeds in the group and in its subgroups can call what it defines; other feeds do not see those names (extractors
and handlers it registers are global, as always). It can also hold templates: `extends: base` looks for `base.yaml`
in the group libraries of the feed, nearest first, then in the workspace `lib/`. A template can extend a template
of the same name further out, so `feeds/news/lib/base.yaml` may start with `extends: base` to refine
`lib/base.yaml`. A broken group library disables only the feeds of its group.

## Feed identity

A feed is identified by its `suffix`: it is served at `/feed/<suffix>`, and its stored output, history,
subscriptions and run records are kept under that name. A suffix may contain ASCII letters, digits, `.`, `-`, `_`
and `~`, and must be unique regardless of case. Without `suffix:` the directory name is used.

Changing the suffix in the file makes it a new feed with empty history. To keep the state, use **Rename suffix**
in the console (feed context menu or toolbar): it rewrites the suffix in `feed.yaml` and moves the stored state.

`id:` (32 lowercase hex characters) is optional. A feed that sets one keeps its state under the id, so it survives
any change of suffix or directory, and it is also served at `/feed/uuid:<id>`. The server never writes ids into
`feed.yaml` itself.

## Using with NewsBlur

Feedxcavator works with [NewsBlurMod](https://github.com/GChristensen/NewsBlurMod). The original NewsBlur
throttles incoming WebSub push requests, so it cannot receive real-time updates smoothly. Because NewsBlur(Mod)
runs inside Docker, Feedxcavator should either also run in a container or be reached by a domain name other than
localhost when both are on the same machine. See the Docker documentation for details.

## Development

```
.venv\Scripts\python -m pytest                 # all tests
cd frontend && npm install && npm run build    # rebuilds feedxcavator/static/console
```

The built console is committed under `feedxcavator/static/console`, so no build step is needed at run time.

### Components

| Area | Built on |
|---|---|
| Web server and console API | Django on uvicorn (ASGI), django-ninja; the console is a Vue application |
| HTTP fetching | curl_cffi, with browser impersonation |
| Browser | One Playwright-API interface: `browser-engine: patchright` (default), `camoufox` or `playwright` |
| Selectors | parsel (CSS and XPath), JMESPath |
| Automatic extraction | feedparser (existing feeds), extruct (JSON-LD, microdata, OpenGraph), a repeated-structure heuristic, trafilatura (full text) |
| Scheduler and queue | A croniter loop in the worker and a small database queue table |
| Telegram | Telethon, connected per call |
| Database | SQLite in WAL mode by default, PostgreSQL through `database-connection-url` |

## License

(c) g/christensen

Distributed under the GNU GPL v3.
