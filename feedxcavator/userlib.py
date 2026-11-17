"""The user library: everything the prelude puts into the namespace of user code (UC2-2, UC2-21, HY-2).

The API tab of the console is generated from the docstrings of this module (`api_reference`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html as _html
import inspect
import pprint as _pprint
import re
import secrets
import time
import traceback
from urllib.parse import quote

from feedxcavator import (applog, conf, context, extraction, fetch, filters, output, reply, selectors as sel,
                          storage, workspace)

# registration ---------------------------------------------------------------------------------


def _decorator(kind, name, **meta):
    if callable(name):  # used without arguments: the function name is the registered name
        workspace.register(kind, name.__name__, name, **meta)
        return name

    def decorate(fn):
        workspace.register(kind, name, fn, **meta)
        return fn

    return decorate


def extractor(name):
    """@extractor("name"): registers an extractor, a function of a feed that returns headlines.
    Feeds refer to it with `extractor: name`. A feed directory that defines exactly one extractor
    uses it without naming it."""
    return _decorator("extractor", name)


def handler(name, auth=False):
    """@handler("name", auth=False): registers an HTTP handler at /handler/<name>. The function
    receives the request parameters named by its arguments, or the whole request when its only
    argument is called `request`. With auth=True the x-feedxcavator-auth header must carry the API token."""
    return _decorator("handler", name, auth=bool(auth))


def task_function(name):
    """@task_function("name"): registers a function for `call`, `pre` and `post` in tasks.yaml.
    `pre` and `post` functions receive the task context (task, trigger, feeds, results)."""
    return _decorator("task_function", name)


def proxy(name, host=None, port=None, user=None, password=None, scheme="http"):
    """proxy("name", host=, port=, user=, password=, scheme="http"): defines a proxy that feeds
    (`fetch.proxy`) and fetch_url (`proxy=`) refer to by name."""
    definition = {"host": host, "port": port, "user": user, "password": password, "scheme": scheme}
    workspace.register("proxy", str(name), definition)
    return definition


# hashing, encoding, misc ------------------------------------------------------------------------


def timestamp():
    """The current time in milliseconds."""
    return int(time.time() * 1000)


def md5(s):
    """Hex MD5 of a string."""
    return hashlib.md5(_bytes(s)).hexdigest()  # noqa: S324


def sha256(s):
    """Hex SHA-256 of a string."""
    return hashlib.sha256(_bytes(s)).hexdigest()


def sha1_sign(data, key):
    """Hex HMAC-SHA1 of `data` signed with `key`."""
    return hmac.new(_bytes(key), _bytes(data), hashlib.sha1).hexdigest()


def generate_uuid():
    """A new UUID: 32 lowercase hex characters."""
    return storage.new_id()


def generate_random(byte_length):
    """A random hex string of `byte_length` bytes."""
    return secrets.token_hex(int(byte_length))


def to_hex_string(data):
    """Hex representation of bytes."""
    return bytes(data).hex()


def _bytes(value) -> bytes:
    return value if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8")


def url_encode_utf8(s):
    """Percent-encodes a string for use in a URL."""
    return quote(str(s), safe="")


def url_safe_base64enc(s):
    """URL-safe Base64 of a string, without padding."""
    return base64.urlsafe_b64encode(_bytes(s)).decode("ascii").rstrip("=")


def url_safe_base64dec(s):
    """Decodes URL-safe Base64 to a string."""
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")


def base64enc(s):
    """Base64 of a string."""
    return base64.b64encode(_bytes(s)).decode("ascii")


def base64dec(s):
    """Decodes Base64 to a string."""
    return base64.b64decode(str(s) + "=" * (-len(str(s)) % 4)).decode("utf-8")


def base64dec_bytes(s):
    """Decodes Base64 to bytes."""
    return base64.b64decode(str(s) + "=" * (-len(str(s)) % 4))


def regex_escape(s):
    """Escapes a string for use in a regular expression."""
    return re.escape(str(s))


def pprint(value):
    """Pretty-prints a value."""
    _pprint.pprint(value)


def html_unescape(s):
    """Replaces HTML entities with their characters."""
    return _html.unescape(s) if s is not None else None


def html_sanitize(s):
    """Escapes <, >, ", ' and & for inclusion in HTML."""
    return _html.escape("" if s is None else str(s), quote=True)


def html_untag(s):
    """Removes HTML tags, keeping the text."""
    return sel.text(s) if s else s


def html_render(nodes):
    """Outer HTML of a node or a list of nodes."""
    if isinstance(nodes, (list, tuple)):
        return "".join(sel.outer_html(n) or "" for n in nodes)
    return sel.outer_html(nodes)


def parse_html(s):
    """Parses an HTML string into a selector tree."""
    return sel.as_doc(s)


def find_header(response, header):
    """Value of a response header, case-insensitively."""
    if response is None:
        return None
    return response.header(header) if hasattr(response, "header") else dict(response.get("headers") or {}).get(header)


def safe_call(fn, *args, **kwargs):
    """Calls a function and returns its result, or None (printing the traceback) when it raises."""
    try:
        return fn(*args, **kwargs)
    except context.JobCancelled:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return None


def safely_repeat(fn, times=2):
    """Calls a function up to `times` times until it does not raise. Returns None when all attempts fail."""
    for _ in range(max(1, times)):
        try:
            return fn()
        except context.JobCancelled:
            raise
        except Exception:  # noqa: BLE001, S112
            continue
    return None


# URLs -----------------------------------------------------------------------------------------


def get_app_host():
    """The base URL of the server: the host of the current request, or `hostname` of the configuration."""
    return context.request_host.get() or conf.app_host() or ""


def get_feed_url(feed):
    """The public URL of a feed."""
    return output.get_feed_url(feed)


def redirect_url(url):
    """A link to this server that redirects to `url`."""
    return f"{get_app_host()}/redirect/{generate_random(10)}/{url_encode_utf8(url)}"


def redirect_url_b64(url):
    """As redirect_url, with the target Base64-encoded so it is not visible in the link."""
    return f"{get_app_host()}/redirect-b64/{generate_random(10)}/{url_safe_base64enc(url)}"


def forward_url(url, referer, https=False, proxy=None):
    """A link to this server that fetches `url` with the given Referer (and proxy) and returns the
    result. Used for media that needs a specific referer."""
    host = conf.secure_host() if https else get_app_host()
    referer = referer or ""
    link = f"{host}/forward/{generate_random(10)}/?url={url_encode_utf8(url)}&referer={url_encode_utf8(referer)}"
    return link + (f"&proxy={url_encode_utf8(proxy)}" if proxy else "")


def forward_url_b64(url, referer, https=False, proxy=None):
    """As forward_url, with the target and referer Base64-encoded."""
    host = conf.secure_host() if https else get_app_host()
    link = f"{host}/forward-b64/{url_safe_base64enc(f'{url};;{referer or ""}')}"
    return link + (f"?proxy={url_encode_utf8(proxy)}" if proxy else "")


# storage ----------------------------------------------------------------------------------------


def find_feed(suffix_or_title):
    """Finds a feed by suffix, then by title."""
    return workspace.get_registry().find_feed(suffix_or_title)


def fetch_feed_output(feed):
    """The stored output of a background feed: {"output", "content_type"}, or None."""
    return storage.fetch_feed_output(feed if isinstance(feed, str) else feed["key"])


def store_feed_output(feed, result):
    """Stores output (the result of produce_feed_output) for a feed."""
    feed_id = feed if isinstance(feed, str) else feed["key"]
    storage.store_feed_output(feed_id, result["output"], result["content_type"], result.get("item_count", 0))


def store_image(data, content_type="image/jpeg", source_url=""):
    """Stores bytes in the blob store and returns the URL they are served at (/image/<id>)."""
    blob = storage.store_blob(bytes(data), content_type, source_url)
    return f"{get_app_host()}/image/{blob.id}"


# Clojure-style helpers over plain dicts and sequences (HY-2). None of them mutates its arguments. ----


def _k(key):
    """Hy keywords used as keys mean the string key of the same (mangled) name."""
    name = getattr(key, "name", None)
    if name is not None and type(key).__name__ == "Keyword":
        return name.replace("-", "_")
    return key


def _call(f):
    return f if callable(f) else (lambda x: x.get(f) if hasattr(x, "get") else x[f])


def assoc(coll, *kvs):
    """(assoc m k v ...): a copy of a dict (or list) with the keys set."""
    if len(kvs) % 2:
        raise TypeError("assoc expects an even number of key/value arguments")
    if isinstance(coll, (list, tuple)):
        result = list(coll)
        for i in range(0, len(kvs), 2):
            result[kvs[i]] = kvs[i + 1]
        return result
    result = dict(coll or {})
    for i in range(0, len(kvs), 2):
        result[_k(kvs[i])] = kvs[i + 1]
    return result


def dissoc(m, *keys):
    """(dissoc m k ...): a copy of a dict without the keys."""
    drop = {_k(k) for k in keys}
    return {k: v for k, v in (m or {}).items() if k not in drop}


def update(m, key, f, *args):
    """(update m k f & args): a copy of a dict with the value of k replaced by (f old & args)."""
    key = _k(key)
    result = dict(m or {})
    result[key] = f(result.get(key), *args)
    return result


def get_in(m, keys, default=None):
    """(get-in m [k1 k2 ...]): the value at a path of keys, or the default."""
    for key in keys:
        key = _k(key)
        try:
            m = m[key]
        except (KeyError, IndexError, TypeError):
            return default
    return m


def assoc_in(m, keys, value):
    """(assoc-in m [k1 k2 ...] v): a copy with the value at a path of keys set."""
    keys = [_k(k) for k in keys]
    if not keys:
        return value
    head, rest = keys[0], keys[1:]
    result = list(m) if isinstance(m, (list, tuple)) else dict(m or {})
    child = (result[head] if isinstance(result, list) else result.get(head)) if rest else None
    if isinstance(result, list):
        result[head] = assoc_in(child, rest, value) if rest else value
    else:
        result[head] = assoc_in(child, rest, value) if rest else value
    return result


def update_in(m, keys, f, *args):
    """(update-in m [k1 k2 ...] f & args): a copy with the value at a path replaced by (f old & args)."""
    return assoc_in(m, keys, f(get_in(m, keys), *args))


def merge(*maps):
    """(merge m1 m2 ...): a new dict with the keys of all, later ones winning."""
    result = {}
    for m in maps:
        if m:
            result.update(m)
    return result


def select_keys(m, keys):
    """(select-keys m [k ...]): a dict with only the given keys."""
    keys = [_k(k) for k in keys]
    return {k: m[k] for k in keys if k in (m or {})}


def distinct_by(f, coll):
    """(distinct-by f coll): the items with a distinct (f item), keeping the first of each."""
    f = _call(f)
    seen, result = set(), []
    for item in coll or []:
        marker = f(item)
        try:
            hash(marker)
        except TypeError:
            marker = repr(marker)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def keep(f, coll):
    """(keep f coll): the non-None results of (f item)."""
    f = _call(f)
    return [r for r in (f(item) for item in coll or []) if r is not None]


def mapcat(f, *colls):
    """(mapcat f coll): the concatenation of the results of (f item)."""
    result = []
    for items in zip(*colls):
        result.extend(f(*items) or [])
    return result


def partition(n, coll, step=None):
    """(partition n coll): lists of n items each. An incomplete last part is dropped."""
    items = list(coll or [])
    step = step or n
    return [items[i:i + n] for i in range(0, len(items) - n + 1, step)]


def partition_all(n, coll):
    """(partition-all n coll): lists of n items each, the last one possibly shorter."""
    items = list(coll or [])
    return [items[i:i + n] for i in range(0, len(items), n)]


def group_by(f, coll):
    """(group-by f coll): a dict from (f item) to the list of items."""
    f = _call(f)
    result = {}
    for item in coll or []:
        result.setdefault(f(item), []).append(item)
    return result


def frequencies(coll):
    """(frequencies coll): a dict from each distinct item to its count."""
    result = {}
    for item in coll or []:
        result[item] = result.get(item, 0) + 1
    return result


# the namespace ------------------------------------------------------------------------------------


class _Telegram:
    """telegram.get_channel_messages(channel), telegram.notify(message, chat_id)."""

    @staticmethod
    def get_channel_messages(channel, limit=26, proxy=None):
        """Headlines for the most recent messages of a public Telegram channel (up to 26). The call goes
        through the proxy named by `proxy` (False for a direct connection), otherwise the feed's
        `fetch.proxy`, otherwise the proxy named `default` when `telegram.default-proxy` is on."""
        from feedxcavator import telegram as tg

        return tg.get_channel_messages(channel, limit, proxy)

    @staticmethod
    def notify(message, chat_id, proxy=None):
        """Sends a Telegram text message to a chat. The proxy is chosen as for `get_channel_messages`."""
        from feedxcavator import telegram as tg

        return tg.notify(message, chat_id, proxy)


telegram = _Telegram()


def browser(profile="default", proxy=None):
    """with browser(profile="default", proxy=None) as b: opens a stealth browser from the worker's pool,
    connecting through the proxy named by `proxy` when given ((with-browser [b :proxy "default"]) in Hy).
    b.open(url, wait_for=None, scroll=0) returns a page with html(), apply_selectors(feed), click(),
    fill(), wait_for(selector, timeout, required=True) (False on a timeout when not required), evaluate(script, arg), screenshot(selector), solve_captcha(), text(), url and
    cookies()."""
    from feedxcavator import browser as _browser

    return _browser.browser(profile, proxy)


def solve_captcha(url, profile="default", timeout=60, watch=10, proxy=None):
    """(solve-captcha url :profile "default" :proxy None): opens `url` in a pool browser and passes a Cloudflare
    challenge there with the system mouse, so later visits with the profile (and fetch_url calls to the host) get
    through. Clearance is bound to the address the challenge came from: solve it through the proxy the site is
    crawled through.
    A challenge may show up some time after the page opens: `watch` is how long to wait for one. True when the
    page is through. In an open browser, page.solve_captcha(watch=...) does the same on that page."""
    with browser(profile, proxy) as b:
        page = b.open(url, timeout=timeout)
        try:
            return page.solve_captcha(timeout, watch=watch)
        finally:
            page.close()


def load_js(name, wrap=True):
    """(load-js "cards"): a script for page.evaluate() from a file in the directory of the workspace file that
    calls it. ".js" is added to a name without a suffix. The file defines `function main()` (or
    `async function main(arg)`); evaluate() returns what main returns, and `(.evaluate page js arg)` passes
    `arg` to main. The file runs in a function scope of its own, so its names do not leak into the page. Names
    it sets on `window` persist between calls on the same page. `:wrap False` returns the file as is. The file is read at
    each call, so edits apply without a workspace reload (the workspace does not watch .js files)."""
    from pathlib import Path

    caller = inspect.currentframe().f_back.f_code.co_filename  # workspace files compile under their relative path
    root = conf.workspace_dir().resolve()
    caller_path = Path(caller)
    if caller_path.is_absolute() or caller.startswith("<"):
        raise ValueError(f"load-js: called from {caller}, which is not a workspace file")
    name = str(name)
    if not Path(name).suffix:
        name += ".js"
    target = (root / caller_path.parent / name).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"load-js: {name} is outside the workspace")
    code = target.read_text(encoding="utf-8")
    if not wrap:
        return code
    if not re.search(r"\bfunction\s+main\s*\(|\b(?:const|let|var)\s+main\s*=", code):
        raise ValueError(f"load-js: {name} does not define a main function")
    # the newline after the code ends a // comment on the last line of the file
    # a function expression: Playwright calls it with evaluate's arg
    return f"async (arg) => {{\n{code}\nreturn await main(arg);\n}}"


class _Log:
    """log.write([level,] message)"""

    write = staticmethod(applog.write)


log = _Log()

CATEGORIES = [
    ("Registration", [extractor, handler, task_function, proxy]),
    ("Fetching", [fetch.fetch_url, fetch.get_last_http_response, fetch.get_last_http_error,
                  fetch.get_last_network_error, fetch.get_last_conversion_error, fetch.log_fetch_errors, browser, solve_captcha, load_js]),
    ("Selectors", [sel.q, sel.q1, sel.text, sel.attr, sel.inner_html, sel.outer_html, sel.extract,
                   sel.apply_selectors, parse_html, html_render, html_unescape, html_sanitize, html_untag,
                   sel.parse_date]),
    ("Extraction", [extraction.parse_html_page, extraction.parse_pages, extraction.parse_page_range,
                    extraction.fetch_document, extraction.default_feed_parser, output.produce_feed_output,
                    output.xml_format]),
    ("Filtering", [filters.filter_history, filters.filter_history_update, filters.filter_history_by_guid_update,
                   filters.filter_content, filters.filter_headlines, filters.add_filter_word,
                   filters.remove_filter_word, filters.add_filter_regex, filters.remove_filter_regex,
                   filters.list_word_filter]),
    ("URLs", [sel.fix_relative_url, redirect_url, redirect_url_b64, forward_url, forward_url_b64, get_feed_url,
              get_app_host, url_encode_utf8, base64enc, base64dec, base64dec_bytes, url_safe_base64enc,
              url_safe_base64dec]),
    ("Utilities", [md5, sha256, sha1_sign, generate_uuid, generate_random, to_hex_string, timestamp,
                   regex_escape, pprint, find_header, safe_call, safely_repeat]),
    ("Collections", [assoc, dissoc, update, get_in, assoc_in, update_in, merge, select_keys, distinct_by, keep,
                     mapcat, partition, partition_all, group_by, frequencies]),
    ("Responses", [reply.text_page, reply.html_page, reply.json_page, reply.rss_page, reply.web_page,
                   reply.attachment_page, reply.redirect_to, reply.no_content, reply.permission_denied,
                   reply.page_not_found, reply.internal_server_error]),
    ("Storage", [storage.store_object, storage.fetch_object, storage.delete_object, find_feed,
                 fetch_feed_output, store_feed_output, store_image]),
    ("Logging", [applog.write]),
    ("Telegram", [_Telegram.get_channel_messages, _Telegram.notify]),
]

# Hy spellings that cannot be derived by mangling
HY_ALIASES = {
    "filter-history!": filters.filter_history_update,
    "filter-history-by-guid!": filters.filter_history_by_guid_update,
    "store-object!": storage.store_object,
    "delete-object!": storage.delete_object,
    "store-feed-output!": store_feed_output,
    "base64dec->bytes": base64dec_bytes,
    "str->html": parse_html,
}

MACROS = [
    ("(defextractor name [feed] body...)", "Defines and registers an extractor (see `extractor`)."),
    ("(defhandler name [a b] body...)", "Defines and registers a handler. `(defhandler ^:auth name [a b] ...)` "
                                         "requires the API token. `(defhandler name request ...)` receives the whole request."),
    ("(defproxy name :host ... :port ... :user ... :password ...)", "Defines a proxy."),
    ("(deftaskfn name [ctx] body...)", "Defines and registers a task function for tasks.yaml."),
    ("(with-browser [b :profile \"default\"] body...)", "Runs the body with a browser from the worker's pool bound to `b`."),
    ("(log-fetch-errors body...)", "Every failed fetch inside the body writes an error to the log."),
    ("(safe-call form)", "Evaluates the form; on an exception prints the traceback and returns None."),
    ("(safely-repeat form)", "Evaluates the form, once more if it raises. `safely-repeat3` tries three times."),
    ("($ node \"selector\")", "All matches of a selector. A literal selector is checked when the file loads. "
                              "Also accepts an Enlive-style vector: ($ page [:div.post :> :a])."),
    ("($1 node \"selector\")", "The first match of a selector, or None."),
    ("(defselectors name :item \"...\" :title \"...\" ...)", "Defines a reusable selector set; (name doc) returns headlines."),
    ("-> ->> as-> some-> cond-> doto", "Threading macros."),
    ("(:key m)", "Keyword lookup on dicts, feeds and headlines; returns None for a missing key."),
]


def _namespace() -> dict:
    ns = {}
    for _, functions in CATEGORIES:
        for fn in functions:
            ns[fn.__name__] = fn
    ns["write"] = applog.write
    ns.update({"telegram": telegram, "log": log, "browser": browser, "fetch_url": fetch.fetch_url})
    ns["production"] = lambda: bool(conf.get("production"))
    return ns


def _patch_keyword():
    """HY-3: `(:title h)` works on headline dicts and returns None for a missing key."""
    from hy.models import Keyword
    from hy.reader import mangle

    if getattr(Keyword, "_fx_patched", False):
        return

    def call(self, data, default=None):
        if data is None:
            return default
        name = mangle(self.name)
        try:
            return data[name]
        except (KeyError, IndexError):
            return default
        except TypeError:
            return getattr(data, name, default)

    Keyword.__call__ = call
    Keyword._fx_patched = True


def install(module):
    """Evaluates the prelude into the namespace of a user module (UC2-2)."""
    import hy  # noqa: F401 - installs the Hy import hook
    from hy.reader import mangle

    import feedxcavator.userlib as this

    _patch_keyword()
    ns = module.__dict__
    ns.update(_namespace())
    for name, fn in HY_ALIASES.items():
        ns[mangle(name)] = fn
    ns["_fx"] = this
    ns["hy"] = hy
    # operators as functions (`(update m "n" + 1)`), the threading macros, and the DSL
    hy.eval(hy.read_many("(import hy.pyops *) (require hyrule [-> ->> as-> some-> doto]) "
                         "(require feedxcavator.hylib *)"), module=module)


def _signature(fn) -> str:
    try:
        return str(inspect.signature(fn))
    except (TypeError, ValueError):
        return "(...)"


def api_reference() -> list[dict]:
    """UI-19, UC2-21: the reference shown on the API tab, generated from the docstrings."""
    hy_names = {id(fn): name for name, fn in HY_ALIASES.items()}
    sections = []
    for category, functions in CATEGORIES:
        entries = []
        for fn in functions:
            name = fn.__name__
            if category == "Telegram":
                name = f"telegram.{name}"
            if category == "Logging":
                name = "log.write"
            entries.append({
                "name": name,
                "hy": hy_names.get(id(fn)) or name.replace("_", "-"),
                "signature": _signature(fn),
                "doc": inspect.getdoc(fn) or "",
            })
        sections.append({"category": category, "entries": entries})
    sections.append({"category": "Hy macros", "entries": [
        {"name": form, "hy": form, "signature": "", "doc": doc} for form, doc in MACROS]})
    return sections
