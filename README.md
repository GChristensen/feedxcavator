# Feedxcavator 4

Feedxcavator is a self-hosted, single-user server that converts web pages and other data sources into feeds
(RSS, Atom, JSON Feed or plain JSON) and serves them. Simple feeds are described in YAML. Anything YAML cannot
express is written as Python or [Hy](https://hylang.org/) code next to the feed, so the extracted content can be
transformed in any way: sorted, filtered, merged with other sources, enriched from linked pages. Slow feeds are
extracted in the background on a schedule, and a built-in [WebSub](https://en.wikipedia.org/wiki/WebSub) hub
pushes their updates to feed readers as soon as they are ready.

Feed definitions and code live as plain files in a workspace directory, which can be edited in the web console,
in an IDE, or kept under git. The server is written in Python with Django, and the console in Vue.

The [manual](feedxcavator/manual.md) describes everything from the first feed to custom extractors, browsers and
HTTP handlers. The console shows it on its **Manual** tab.

## Features

- Feeds from CSS, XPath or JMESPath selectors, with several pages read by number or through a "next" link.
- Automatic extraction: a feed the site already publishes, structured data on the page, or a repeated list of
  articles; optionally the full text of each linked article.
- Filters: history of seen items, duplicates, word filters, regular expressions, limits.
- Python and Hy code for anything the selectors cannot do: per-headline transforms, post-processing of the
  whole list, custom extractors, and HTTP handlers for your own API.
- Background tasks on cron schedules, with a run history and WebSub push.
- Requests that imitate a real browser, named proxies, and a pool of real browsers (patchright, camoufox or
  playwright) for pages that need JavaScript, with persistent profiles and help with Cloudflare challenges.
- Telegram channels as feeds.
- A web console with an editor, feed testing, selector suggestions, and a reference of the user library.

A feed can be this short:

```yaml
title: My first feed
source: https://example.com
selectors:
  item: article
  title: h2::text
  link: h2 a
```

Saved as `workspace/feeds/my-first-feed/feed.yaml`, it is served at `http://<host>:10000/feed/my-first-feed`.

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

Optional parts are installed separately: the camoufox and playwright browser engines, pyautogui for Cloudflare
challenges, and psycopg for PostgreSQL (`pip install "feedxcavator[postgres]"`). The manual describes the browser
engines and their setup.

Commands (`python -m feedxcavator <command>`):

| Command | Purpose |
|---|---|
| `run` (default), `--dev` | supervisor: web + worker; `--dev` reloads the web process on code changes |
| `web`, `worker` | one process only |
| `set-password [--user U] [--password P]` | create the user or set its password |
| `telegram-login` | interactive Telegram login, creates `data/telegram/` |
| `manage <django command>` | any Django management command |

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
