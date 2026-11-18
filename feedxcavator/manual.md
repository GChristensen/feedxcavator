# Feedxcavator manual

This manual goes from a first feed written in a few lines of YAML to feeds written in code, background tasks,
browsers and custom HTTP handlers. 

## How Feedxcavator works

A *feed* is a directory under `workspace/feeds/` with a `feed.yaml` file in it. The definition names a source
(a web page, a JSON API, another feed) and says how to find headlines in it. The server serves the result at
`/feed/<suffix>`, where the suffix is the directory name unless the definition sets another.

A feed is extracted in one of two ways:

- On request. Each visit to the feed URL fetches the source and builds the feed again. This is the default and
  suits feeds that are quick to build.
- In the background. A feed that belongs to a *task* is extracted when the task runs, on a schedule or by hand.
  The result is stored, served to everyone who visits the URL, and pushed to WebSub subscribers.

Everything you write lives as plain files in the workspace: feed definitions, Python and Hy code, task and
schedule files. You can edit them in the web console, in an IDE, or keep them under git. The server notices
changes to the files and reloads the workspace.

## The console

The console is at `http://<host>:10000/console`. Its tabs:

| Tab | What it is for |
|---|---|
| Workspace | the file tree, the editor and the output pane; feeds are created, tested and debugged here |
| Tasks | background tasks, their feeds, and Run now |
| Schedules | when tasks run; Run now |
| Runs | the history of task and feed runs, with durations, headline counts and results |
| Subscriptions | WebSub subscriptions: their feeds, callbacks, status and expiry |
| Word filters | lists of words and regular expressions that remove headlines |
| Log | messages written by the server and by your code |
| Settings | the API token, the subscription URL, backup and restore, purging stored images, restart |
| API | every function of the user library, with Python and Hy names |
| Manual | this text |

### The Workspace tab

The tree on the left shows the workspace. Right-click a feed for its actions: **Test**, **Open feed URL**,
**Subscribe**, **Run feed task**, **Suggest selectors**, **New code file**, **Rename folder** and **Delete**.
Right-click a folder under `feeds/` for **New feed**, and the empty part of the tree for **Reload workspace**.
Files can be dragged to another folder.

Below the tree, the outline lists what the open file defines: extractors, handlers, functions, tasks. Click an
entry to jump to it; right-click a task to run it or list its feeds.

The toolbar above the editor follows the open file. For a `feed.yaml` it offers **Save**, **Test**, **Suggest
selectors**, **Open** (the feed URL), **Subscribe** and **Delete**. For `scratch.hy` it offers **Run**. Ctrl S
saves the file, or runs it in the case of the scratch file.

**Test** extracts the feed without publishing it and shows the headlines in the output pane. A test skips the
`history` filter steps, so it shows the same headlines every time and does not mark them as seen.

The header shows whether the workspace loaded. When a file fails to load, it shows the number of errors; click it
to see them in the output pane. Files with errors are marked in the tree and in the editor tabs.

Ctrl K (or the search box in the header) opens a feed or a file by name.

## Your first feed

Create the file `workspace/feeds/my-first-feed/feed.yaml`, or use **New feed** in the console, with this text:

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

The feed is served at `http://<host>:10000/feed/my-first-feed`. Each time the URL is visited, Feedxcavator reads
the pages from https://example.com/page/1 to https://example.com/page/5, finds every element matched by the
`item` selector, and builds a headline from each of them with the other selectors, which are applied inside the
item.

A link is taken from the `href` attribute of the matched element and an image from its `src` attribute, unless
the selector names an attribute. The definition above takes the image from `data-src`, and from `src` when
`data-src` is absent. Relative links are made absolute against the source URL.

Before writing selectors by hand, try **Suggest selectors**: it proposes a `selectors` block for the source page.
Then press **Test** to see what the feed produces.

## Selectors

Selectors are full CSS as implemented by [parsel](https://github.com/scrapy/parsel) and cssselect: any combinator,
attribute operator, `:not()`, `:nth-child()` and so on can be used. Feedxcavator adds a few things at the end of a
selector:

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

## Reading several pages

The `pages` block reads more than one page of the source. Numbered pages are built by appending `path`, with `{n}`
replaced by the page number, to the source URL:

```yaml
pages:
  include-source: true         # also read the URL in `source` itself
  path: '/?page={n}'
  start: 2
  end: 3
  increment: 1
  delay: 0                     # seconds between page requests
```

A site without page numbers can be followed through its "next" link instead:

```yaml
pages:
  next: 'a.next::attr(href)'
  max: 5
```

`source` can also be a list of URLs; headlines from all of them go into one feed.

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

`fulltext: true` works with selectors too. It fetches every linked page, so the feed gets slower with each
headline; such feeds are good candidates for background extraction.

## Filtering headlines

The `filter` list removes headlines. Its steps run in the order they are written:

```yaml
filter:
  - history: {key: link, keep: 1000}
  - dedupe: link
  - wordfilter: {name: default, fields: [title, summary]}
  - exclude: {field: title, regex: '^Sponsored'}
  - limit: 50
```

`history` drops the headlines that the previous extractions of the feed already returned, so a feed shows only
new items. `key` names the field that identifies a headline (`link` by default). The history keeps the last
`keep` keys (1000 by default), or with `keep_days` the keys seen in that many days.

`dedupe` keeps the first of the headlines that have the same value in a field. `exclude` drops headlines whose
field matches a regular expression; the field is `title` when it is not given. `limit` keeps the first so many
headlines.

### Word filters

A word filter is a named list of words and regular expressions. The `wordfilter` step names the filter and the
fields to check; without a name the filter called `default` is used, and without fields only the title is
checked.

Filters are edited on the **Word filters** tab, one entry per line. A plain word matches case-insensitively
anywhere in the field. An entry written as `/expr/` is a regular expression; a word boundary `\b` in it helps to
avoid matching parts of longer words. The tab shows which feeds use each filter.

Filters can also be changed from code (for example, from `scratch.hy`) with `add_filter_word`,
`remove_filter_word`, `add_filter_regex`, `remove_filter_regex` and `list_word_filter`:

```hy
(add-filter-regex "default" r"\bsponsored\b")
```

The same operations are available over HTTP, see [Word filters over HTTP](#word-filters-over-http).

## Fetch options and output formats

The `fetch` block controls how the source is downloaded:

```yaml
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
```

Requests go through curl_cffi and imitate a real browser. `impersonate` names the curl_cffi fingerprint (`chrome`
by default, also `firefox`, `safari` and versioned names such as `chrome124`). Proxies are described in
[Proxies](#proxies) and the browser backend in [Browsers](#browsers).

Links in the page are made absolute against the address the page was finally served from, after redirects, or
against its `<base href>` when it has one.

A feed URL of a feed without tasks is extracted while the reader waits. That extraction has a time budget,
`request-timeout` in `config.yml` (seconds, 50 by default): each download is cut to the time left, and retries
that would not fit are skipped, so the reader gets the error headline instead of a timeout. When the server is
behind a reverse proxy, keep `request-timeout` below the proxy's own limit (`proxy_read_timeout` in nginx, 60
seconds by default). Background extraction and tests in the console have no budget.

`output` selects the format: `rss` (the default), `atom`, `json-feed` or `json`. RSS, Atom and JSON Feed carry
the standard headline fields; plain JSON keeps every field the headlines have.

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
  backend: http
  # ... see "Fetch options and output formats"
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
pages:
  # ... see "Reading several pages"
filter:
  # ... see "Filtering headlines"
fulltext: false                # true: fetch each linked page and use its main text as the summary
transform: function-name       # a function applied to every headline
post_process: function-name    # a function applied to the whole list of headlines
output: rss                    # rss, atom, json-feed or json
tasks: [task-name]             # extract in the background (see "Background extraction")
parallel: true                 # false: run on the sequential worker pool
websub: {enabled: true, partition: 100}  # push updates, by parts of 100 items
expect_empty: false            # true: zero headlines is normal, no zero-headline log warning or highlight in run history
extractor: extractor-name      # the code that produces the headlines instead of the selectors
params: [any additional data, [123, 456]]  # arbitrary data for the code
```

Any other top-level key is kept and available to code as a feed field.

## Organizing the workspace

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

Folders under `feeds/` group feeds. A task can select all feeds of a group by path, and a group can share code
and templates.

### Templates

Feeds that read similar sites can share their common part. `extends: forums/phpbb` merges the feed definition
into `forums/phpbb.yaml`: keys of the feed replace keys of the template, and maps (such as `selectors` or
`fetch`) are merged key by key.

### Group libraries

A group folder under `feeds/` may have its own `lib/` directory. Its code loads before the feeds of the group, and
the feeds in the group and in its subgroups can call what it defines; other feeds do not see those names
(extractors and handlers it registers are global, as always).

A group library can also hold templates. `extends: base` looks for `base.yaml` in the group libraries of the
feed, nearest first, then in the workspace `lib/`. A template can extend a template of the same name further out,
so `feeds/news/lib/base.yaml` may start with `extends: base` to refine `lib/base.yaml`.

### Errors while loading

A broken feed file disables only that feed, and a broken group library only the feeds of its group. A broken
`lib/` file keeps the previously loaded workspace running. If a `lib/` file fails at startup, there is no previous
state to keep, so the rest of the workspace loads without that file and the error is reported.

### Feed identity

A feed is identified by its `suffix`: it is served at `/feed/<suffix>`, and its stored output, history,
subscriptions and run records are kept under that name. A suffix may contain ASCII letters, digits, `.`, `-`, `_`
and `~`, and must be unique regardless of case. Without `suffix:` the directory name is used, so a directory
whose name is not a valid suffix needs an explicit `suffix`.

When you change the suffix of a `feed.yaml` and save it in the console, the stored state moves to the new suffix.
The save is refused when another feed already has that suffix. A suffix changed outside the console (in an IDE or
by git) makes the feed a new one, with empty history.

`id:` (32 lowercase hex characters) is optional. A feed that sets one keeps its state under the id, so it survives
any change of suffix or directory, and it is also served at `/feed/uuid:<id>`. The server never writes ids into
`feed.yaml` itself.

## Background extraction

A feed without `tasks` is extracted each time its URL is visited. A feed with `tasks` is extracted in the
background: the result is stored and served when a user or a feed reader visits the URL, and pushed to WebSub
subscribers. This suits feeds that need complex or slow processing.

### Tasks

Tasks are defined in `workspace/tasks.yaml`, or on the **Tasks** tab. A feed belongs to a task when it lists the
task in its `tasks` field, or when the task selects it by tag, by path or by suffix:

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

A task may also name `pre` and `post` task functions, which run before and after its feed jobs. `timeout` limits
how long the task may run (written like `45s`, `30m` or `2h`). `concurrency` says what happens when the task is
triggered while it is still running: `skip` (the default) ignores the trigger, `queue` runs the task again after
the current run, and `allow` starts another run at once.

Feeds with `parallel: true` (the default) run on a pool of parallel workers, the others one at a time on the
sequential pool. `worker-parallel` and `worker-sequential` in `config.yml` set the sizes of the pools.

### Schedules

Schedules are in `workspace/schedules.yaml` and are managed on the **Schedules** tab. They use five-field cron
expressions in the local time zone unless a `timezone` is given. A task can be scheduled several times:

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

A schedule can also set `enabled: false`, `misfire: run-once` (run once when a firing was missed while the server
was down; the default `skip` ignores missed firings), and `jitter`, a random delay of each firing of up to that
many seconds.

A task can be started at any time with **Run now** on the Tasks or Schedules tab, and the tasks of a feed with
**Run feed task** in its context menu. The **Runs** tab shows what ran, when, and with what result.

### WebSub

Feeds announce the built-in WebSub hub at `/websub` in their `hub` link. A feed reader that supports WebSub
subscribes there, and gets each new version of a background feed as soon as the task has stored it, without
polling.
`websub: {partition: 100}` in a feed sends large updates in parts of 100 items. The **Subscriptions** tab lists the
subscriptions; it can also rewrite callback URLs in bulk, for example after the reader moved to another address.

Set `websub: false` in `config.yml` to disable the hub.

## Writing code: Python and Hy

When YAML is not enough, write code. Code files sit in the workspace next to the definitions: shared code in
`lib/`, feed-specific code in the feed directory, HTTP handlers in `handlers/`. A `.py` file is Python and a `.hy`
file is [Hy](https://hylang.org/), a Lisp that compiles to Python. The Hy prelude adds a compact DSL for feed code:
definition macros such as `defextractor` and `defhandler`, keyword lookup with `(:title h)`, threading macros, and
Clojure-style collection functions (`assoc`, `update-in`, `group-by` and others) that never modify their
arguments. Python is there for those who would rather not write Lisp; the collection functions are available in it
too.

Both languages can be mixed freely, even in one feed directory, because all files evaluate into one shared
namespace. Hy names are translated into Python names by the usual Hy rules, so a function defined in Hy as
`parse-forum` is called from Python as `parse_forum`, and a Python function `clean_title` is `clean-title` in Hy.
Every Python library installed in the environment can be imported from either language.

The user library (fetching, selecting, filtering, storage, responses) is available in every file without
imports. The **API** tab lists it with signatures in both spellings. A few names that Hy mangling cannot produce
have Hy aliases: `filter-history!` is `filter_history_update` in Python, `store-object!` is `store_object`,
`str->html` is `parse_html`.

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

### The scratch file

`scratch.hy` in the workspace root is a place to try code. It is never loaded with the workspace; **Run** (or
Ctrl S) evaluates it in a copy of the workspace namespace and shows what it prints, or the traceback of an
error, in the output pane. Use it to call a feed's
functions by hand, fetch a page and try selectors on it, or change word filters.

### Logging

`log.write` writes a string, an exception (with its traceback) or any other value to the **Log** tab. A level
can be given as the first argument: `info` (the default), `warning` or `error`.

```python
log.write("warning", f"no items at {feed.source}")
```

## Changing headlines with code

There are three places where code can change what the selectors found, from the smallest to the largest.

A headline is a dict with these fields:

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

RSS, Atom and JSON Feed output ignore other fields; plain JSON output keeps all of them.

### Transform

A function named in `transform` receives one headline and returns a changed copy, None to keep it as it is, or
False to drop it:

```python
def shout(headline):
    return {**headline, "title": headline["title"].upper()}
```

```hy
(defn shout [headline]
  (assoc headline "title" (.upper (:title headline))))
```

```yaml
transform: shout
```

### Post-process

A function named in `post_process` (`post-process` is accepted too) receives the whole list of headlines after
`transform` and before the filter steps, and returns a new list, or None to keep the list as it is:

```python
def by_title(headlines):
    return sorted(headlines, key=lambda h: h["title"])
```

```yaml
post_process: by-title
```

The feed keeps the selectors, so when the source cannot be read it still gets the error headline that the
built-in extraction produces.

### Extractors

An extractor replaces the selectors altogether. The `extractor` field names an extractor function defined in code;
a feed directory with exactly one extractor uses it even without the field. An extractor receives the feed and
returns a list of headlines.

This extractor sorts the headlines found by the feed selectors by title:

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

## Fetching and selecting in code

### Fetching web resources

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
`feed.yaml` is in seconds). With `follow_redirects=False` a redirect is not followed and the 3xx response is
returned; its target is `response.header("location")`.

### Selecting elements

Fetch a page with `as_="html"` and take its parts with the selector functions, which accept the selectors
described in [Selectors](#selectors):

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

### Proxies

Proxies are defined by name under `proxies` in `config.yml`, either as a URL or as a mapping:

```yaml
proxies:
  default: socks5://127.0.0.1:1080
  work:
    scheme: http          # http (the default), https, socks5, socks5h, socks4 or mtproto (see below)
    host: proxy.example.com
    port: 3128
    user: me              # optional
    password: secret      # optional
```

In the URL form, special characters in the user name and password must be percent-encoded. Changes take effect
after a restart.

A fetch uses the proxy named by its `proxy` option, otherwise the one named by `fetch.proxy` of the current
feed. `proxy=False` forces a direct connection. A name that is not defined (or a definition without a host and
port) fails the fetch with a network error, written to the log, rather than connecting directly; the browser
and Telegram calls refuse it too.

The name `default` is not applied to feeds by itself; a feed must name it in `fetch.proxy`. Telegram calls go
through the proxy named by `telegram.use-proxy` when it is set, whatever the feed or the call says. Otherwise they
use their own `proxy` argument, then the `fetch.proxy` of the calling feed, and connect directly when neither names
one. The `/forward` links made by
`forward_url(url, referer, proxy="name")` fetch through the named proxy. WebSub callbacks never use a proxy.

### MTProto proxies

A Telegram proxy (MTProto) is defined with the scheme `mtproto` and its `secret`, in any of these forms; a link
as Telegram shares it can be pasted as is:

```yaml
proxies:
  tg: tg://proxy?server=proxy.example.com&port=443&secret=dd0123456789abcdef0123456789abcdef
  tg2: mtproto://proxy.example.com:443?secret=0123456789abcdef0123456789abcdef
  tg3:
    scheme: mtproto
    host: proxy.example.com
    port: 443
    secret: dd0123456789abcdef0123456789abcdef
```

Only Telegram calls can use it: a fetch or a browser that names it fails, rather than connecting directly. Plain
secrets (32 hex characters) and `dd` secrets work. Fake-TLS secrets (starting with `ee`, the only kind mtg 2
issues) do not, because Telethon cannot speak that protocol; the call fails with an error in the log. When you
deploy a proxy for Feedxcavator, use one that accepts a plain or `dd` secret, such as the official
`telegrammessenger/proxy` image. In code, `(defproxy tg :scheme "mtproto" :host "..." :port 443 :secret "...")`
defines one.

## Images and media

A feed reader loads the images of an item itself, from the reader's computer. That fails when the image exists
only inside the server (a browser screenshot, a Telegram photo), or when the site refuses requests that do not
come from its own pages. Feedxcavator can store the image and serve it, or fetch it on the reader's behalf.

### Saving and serving images

`store_image(data, content_type="image/jpeg", source_url="")` (`store-image` in Hy) stores bytes and returns the
link they are served at, `<host>/image/<id>`. Put the link in the `image` field of a headline, or in an
`<img>` tag of its summary:

```python
@extractor("chart-extractor")
def chart_extractor(feed):
    with browser() as b:
        page = b.open(feed.source, wait_for="#chart")
        png = page.screenshot("#chart")
    return [{"title": "Today's chart", "link": feed.source,
             "image": store_image(png, "image/png", feed.source)}]
```

The same applies to an image that has to be fetched with cookies or a token once:

```python
data = fetch_url(url, headers={"Authorization": f"Bearer {TOKEN}"}, as_="bytes")
if data:
    headline = assoc(headline, "image", store_image(data, "image/jpeg", url))
```

- Each call returns a new link, but identical content is kept on disk only once. `source_url` is only recorded.
- `/image/<id>` is public, so readers open it without logging in. It answers range requests and tells readers to
  cache the image for a year, since the content behind a link never changes.
- The link starts with the address of the current request, or with `hostname` from `config.yml` during
  background extraction (see [Hostnames](#hostnames-a-reader-on-the-same-machine)).
- Stored images are deleted after `service-task.max-age-days` (2 days by default, see
  [Maintenance](#maintenance)), and the Settings tab can purge them all. Old items then show broken images, so
  keep the age above the time your reader keeps items unread.
- With `x-accel` set in `config.yml`, nginx sends the image files itself (`deploy/nginx.conf`, HTTPS in the
  README).

External programs can store images too: `POST /api/images/save` with the image as the request body, its type in
`Content-Type`, and the [API token](#the-api-token) in the `x-feedxcavator-auth` header. The answer is the id as
plain text; the image is then at `/image/<id>`.

```
curl -X POST -H "x-feedxcavator-auth: $TOKEN" -H "Content-Type: image/png" \
     --data-binary @chart.png http://localhost:10000/api/images/save
```

### Forwarding images with a referer and a proxy

Many sites refuse to serve images, and especially video, to a request whose `Referer` is not one of their pages,
and some sites are reachable only through a proxy. `forward_url(url, referer, https=False, proxy=None)`
(`forward-url` in Hy) returns a link to this server that fetches `url` when it is opened, sends `referer` as the
`Referer` header, and passes the answer to the reader:

```python
def forwarded_image(headline):
    if headline.get("image"):
        headline = assoc(headline, "image", forward_url(headline["image"], headline["link"]))
    return headline
```

```hy
(defn forwarded-image [headline]
  (if (:image headline)
      (assoc headline "image" (forward-url (:image headline) "https://example.com/" :proxy "work"))
      headline))
```

Name the function in `transform` of `feed.yaml` (see [Transform](#transform)) to apply it to every headline.

- `proxy` names a proxy from `proxies` in `config.yml` (see [Proxies](#proxies)). Without it the fetch connects
  directly; the feed's own `fetch.proxy` is not applied, because the link is opened outside the feed.
- `https=True` starts the link with `secure-hostname` instead of the current address, for readers that open
  only HTTPS links.
- Nothing is stored: the image is fetched again every time the link is opened, without retries. The `Range`
  header of the reader is passed on, so video can be seeked. An upstream failure answers 502 with the reason.
- The link carries the target and the referer in plain view. `forward_url_b64` takes the same arguments and
  encodes both in Base64 (`/forward-b64/...`), which keeps them out of sight but not secret.
- `/forward` links are public, like `/image`, so anyone who has a link can open it.

Forwarding suits large or changing media and sites that must see each request; storing suits images that are
small, made inside the server, or need credentials to fetch.

## Browsers

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

A page can also be driven: `goto`, `click`, `fill`, `press`, `wait_for`, `wait`, `scroll`, `evaluate` (with
`load_js` for scripts kept in files), `screenshot`, `html`, `text` and `url`. The Browser section of the API tab
describes every method of the browser and the page.

Browsers run in the worker; code in the web process (the console) reaches them over the local RPC. Each
`with browser()` block starts a browser and closes it at the end.

After a page is opened, later `fetch_url` calls to the same host reuse the browser's cookies and user agent
(kept in memory by the process until it restarts), so a browser can pass a challenge and plain fetches can do
the rest.

The browser does not use the proxies defined in `config.yml`: `fetch.proxy` has no effect on
`fetch.backend: browser`.

### Profiles

A profile is a named, persistent browser data directory, `data/browser/<profile>/`, which keeps cookies, local
storage and logins between runs. Profiles need no definition: a new name creates its directory on first use.
Choose one with `browser(profile="shop")` or `(with-browser [b :profile "shop"] ...)`; the default is
`default`, and `fetch.backend: browser` always uses `default`. One profile can be open in one browser at a
time, so jobs on the same profile wait for each other even when the pool has room.

To log in to a site once, open the profile headful (`browser-headless: false`), log in by hand, and later runs
reuse the session. `chrome-profile.sh [profile] [url]` opens Chrome with a pool profile for the patchright and
playwright engines, and `firefox-profile.sh [profile] [url]` opens Camoufox for the camoufox engine. Close the
window before the worker uses the profile.

Profiles are per engine: cookies and sign-ins saved by Chrome do not carry over to camoufox. Use a new profile
name (or clear the folder) when switching engines, and sign in again.

### Security checks

Sites behind a security check (DDoS-Guard, a bot check of their own) show the content only after the check passes
and reloads the page, which can take ten seconds or more. Wait for the content rather than for a fixed time:
`page.wait_for(selector, timeout, required=False)` keeps waiting through the reload and returns False on a timeout
instead of raising, so the code can reload once or give up. The clearance cookies stay in the profile, so usually
only the first page of a run waits.

### Cloudflare challenges

`(solve-captcha url :profile "default")` opens a page in a pool browser and passes a Cloudflare challenge on it
(the "Just a moment..." page or a Turnstile checkbox); in an open browser, `(.solve-captcha page)` does the same
for that page. Both return `True` when the page is through.

Turnstile ignores clicks the browser driver makes, so the real pointer is moved and clicked with pyautogui: three
moves to random points at least 15% of the screen away from its edges, then a click on the checkbox, and a second
try when the challenge stays. The checkbox is found in the page, not at fixed screen coordinates: its place comes
from the widget's box, and page coordinates are mapped to the screen by moving the pointer over the page and
reading where the page saw it. `captcha-position` is the fallback when no widget is found. One solve runs at a
time, since there is one pointer.

This needs pyautogui and a headful browser on a display the worker can reach, with the window not covered by
others:

```
python3 install.py --with-captcha
```

On Linux, when the worker runs as another user than the desktop session, allow it on the desktop display with
`xhost +` (or `xhost +SI:localuser:<user>`) and set `browser-display: ":0"`. Under Xvfb the clicks work too,
but nobody can watch them. Set `captcha-flash: true` to see where the pointer acts while checking a setup.

### Browser settings

The browser is set up in `config.yml`:

| Key | Default | Meaning |
|---|---|---|
| `browser-engine` | `camoufox` | `camoufox` (Firefox based), `patchright` or `playwright` |
| `browser-channel` | `chrome` | the installed browser patchright or playwright drives; the bundled Chromium when it is missing |
| `browser-pool-size` | `1` | how many browsers may run at once; further jobs wait |
| `browser-wait-timeout` | `300` | seconds a job waits for a free browser (or for its profile) before failing |
| `browser-headless` | see below | `true` or `false`; by default headful on Linux when a display or Xvfb is available, otherwise headless |
| `browser-display` | none | Linux: the X display the worker uses when `DISPLAY` is not set, such as `":0"` |
| `captcha-position` | none | `[x, y]`: the screen point `solve-captcha` clicks when it cannot find the Turnstile widget |
| `captcha-flash` | `false` | `solve-captcha` shows a marker at each point the pointer moves to |

Engine and channel are global; profiles cannot set their own. Changes take effect after a restart, because the
browser pool lives in the worker process.

On Linux without a display the worker starts Xvfb, when it is installed, so the browser runs headful, which is
less detectable than headless. Install it with the distribution's package (`xorg-x11-server-Xvfb` on
Fedora/RHEL, `xvfb` on Debian/Ubuntu). `browser-headless: true` forces headless mode.

### Browser engines

| Engine | Browser | Setup |
|---|---|---|
| `camoufox` (default) | a Firefox build with fingerprint spoofing | `install.py --with-browser` |
| `patchright` | the installed Chrome (`browser-channel`), patched against automation detection | `install.py --with-browser=patchright` |
| `playwright` | stock Playwright Chromium, no stealth | `install.py --with-browser=playwright` |

No engine is installed with the core package. `install.py` installs the chosen ones into the virtual environment
and downloads their browsers: the Camoufox build (kept in the user's cache folder, outside the venv), or for
patchright and playwright their Chromium when no Chrome is installed. It then test-launches Camoufox; on Linux a
failure there usually means missing system libraries (GTK 3, libX11-xcb, ALSA), which the distribution's firefox
package brings in. Several engines can be installed at once (`--with-browser=camoufox,patchright`); the one in
use is `browser-engine`, and switching it needs a restart. If the engine set there is not installed, a browser
feed fails with "browser-engine is patchright, but it is not installed".

With camoufox, each profile keeps one fingerprint for the host's OS in
`data/browser/<profile>/feedxcavator-fingerprint.json`, created on the first launch, so the sign-in script and the
worker present the same browser. The file also keeps the host GPU that Camoufox reads by starting a bare browser
for a moment (a short extra window), so this happens once per profile, normally in `firefox-profile.sh`. Delete
the file to get a new identity, or after changing the machine's graphics.

## Telegram

`telegram.get_channel_messages(channel)` returns headlines for the latest messages of a public Telegram channel,
and `telegram.notify(message, chat_id)` sends a text message. Both need `api-id` and `api-hash` under the
`telegram` key of `config.yml` and a one-time login with `python -m feedxcavator telegram-login`.

```yaml
telegram:
  api-id: 123456
  api-hash: 0123456789abcdef0123456789abcdef
  use-proxy: tg       # optional: every Telegram connection, the login included, goes through this proxy
```

Without `use-proxy`, a call goes through the proxy its feed names in `fetch.proxy`, or directly (see Proxies). An
MTProto proxy (see MTProto proxies) can be used only for Telegram.

### Logging in

Log in with your own account: a bot cannot read channel history. The API id and hash come from an app created at
https://my.telegram.org (API development tools). Run the login on the server, in the installation directory:

```
python -m feedxcavator telegram-login --qr
```

With `--qr` it shows a QR code in the terminal. In Telegram on your phone, open Settings > Devices > Link Desktop
Device and scan it. A code is valid for about 30 seconds and a new one follows until you scan it or press Ctrl+C.

Without `--qr` it asks for the phone number and says where Telegram sent the code: to the Telegram app (a chat
from Telegram), by SMS or call, or by email when the account has a login email. Enter nothing at the code prompt to
have it sent another way, when Telegram offers one. Telegram expires a code that was sent in any Telegram chat, so
type it rather than forwarding or pasting it there.

Either way, an account with two-step verification is then asked for its cloud password. The session is stored in
`data/telegram/feedxcavator.session`; keep it private, as it gives access to the account. To log in as someone
else, stop the server, delete that file and run the login again. A session made on another machine with the same
API id and hash can be copied there too.

## Handlers

Handlers define a custom web API for your feeds. A handler registered with the name `my-first-handler` is called
when `http://<host>:10000/handler/my-first-handler` is visited. Its arguments receive the query or form parameters
of the same names. A handler whose only argument is called `request` receives the whole request instead
(`method`, `path`, `headers`, `body`, `params`, `json`).

A handler returns one of the response functions (`text_page`, `json_page`, `web_page`, `redirect_to` and others),
a dict or list that is sent as JSON, or None for an empty response.

```python
@handler("my-first-handler")
def my_first_handler(param):
    log.write(param)
    return text_page("OK")
```

### The API token

With `auth=True` (`^:auth` in Hy) the request must carry the API token in the `x-feedxcavator-auth` header. The
token is generated on the Settings tab. It is stored hashed and shown only once, so copy it when it appears.

This handler gives the stored output of a background feed to an external program and takes the processed output
back:

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

### Word filters over HTTP

The REST API at `/api/wordfilter/<action>` changes word filters from outside the server. The actions are `list`,
`list-words`, `add-word`, `remove-word`, `add-regex` and `remove-regex`; each takes a JSON body and the API token.
The author uses [these](https://gist.github.com/GChristensen/c4be3bb8508ad13d982c2f57ac302eb8) commands for the
[iShell browser extension](https://gchristensen.github.io/ishell).

## Examples

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

## Hostnames: a reader on the same machine

The server is reached from two directions: browsers follow the links written into feed items, and a WebSub
subscriber talks to the hub and is pushed updates. When the reader runs on the same machine and the machine's LAN
name resolves to itself there, one address serves both.

Example: Feedxcavator and NewsSharp, a web feed reader, run on a machine called `lab`. People read news in a
browser on other computers of the LAN. NewsSharp subscribes to Feedxcavator over WebSub.

```yaml
port: 21000
host: 0.0.0.0                     # listen on the LAN too, not only on 127.0.0.1
hostname: 'http://lab:21000'      # websub-hostname and secure-hostname default to it
#secure-hostname: 'https://lab:22000'   # only with an HTTPS reverse proxy, see HTTPS in the README
```

- `hostname` is the base of image, redirect and forward links in feed items, of each feed's `self` link and of its
  `hub` link (`http://lab:21000/websub`). Browsers on the LAN and NewsSharp on `lab` can all open it.
- In NewsSharp, add feeds by the same address, for example `http://lab:21000/feed/city-paper`, so the
  subscription URL equals the feed's `self` link. Feeds without `tasks`, which are extracted when NewsSharp polls
  them, then also get `lab` links: during a request the server writes links with the address the request came to.
- NewsSharp chooses the callback URL and sends it with the subscription. Feedxcavator opens it from `lab` to verify
  the subscription and to push updates, so it may use `localhost` or `lab`. If the callback cannot be opened, the
  subscription is not verified and the log shows "WebSub verification failed".

Check that the name resolves on the server itself (`ping lab` there). If it does not, add `127.0.0.1 lab` to the
hosts file, or keep `hostname` at `lab` and set `websub-hostname: 'http://localhost:21000'`. With the second
option, subscribe at the `localhost` URL. A feed without tasks that NewsSharp polls at `localhost` then gets
`localhost` image links, which do not open on other computers.

## Maintenance

The daily service task (`service-task` in `config.yml`) deletes stored images and log entries older than
`max-age-days`, and prunes old history and run records. Run records are kept for `run-history-days` days.

The Settings tab downloads a backup of the database, optionally with the workspace, and restores one; the
workspace of a backup is unpacked only into an empty workspace directory. The tab can also
purge stored images and restart the server when it runs under the supervisor or the systemd unit. Child processes
(browsers) are stopped on shutdown; when the server is killed forcibly, the children it left behind are stopped on
the next start.

When `repl-port` is set (or `FEEDXCAVATOR_REPL_PORT`), the worker opens a Python/Hy REPL bound to 127.0.0.1 that
shares the namespace of the workspace code.