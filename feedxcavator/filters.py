"""Headline filtering: history, word filters and the filter steps (FD2-10, FD2-11, WF-1, WF-2)."""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from feedxcavator import context

DEFAULT_HISTORY_KEEP = 1000


def _sha(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def history_namespace(feed_or_key) -> str:
    """UC-8: a feed (keyed by its id when it has one, else by its suffix) or any string."""
    key = feed_or_key if isinstance(feed_or_key, str) else (feed_or_key.get("key") or feed_or_key.get("id"))
    return _sha(key)


def _known(namespace: str, hashes: list[str]) -> set[str]:
    from feedxcavator.models import HistoryKey

    known = set()
    for i in range(0, len(hashes), 500):
        known.update(
            HistoryKey.objects.filter(namespace=namespace, key_hash__in=hashes[i:i + 500])
            .values_list("key_hash", flat=True)
        )
    return known


def filter_history(feed_or_key, headlines, key="link"):
    """Drops headlines whose key (the link by default) is in the history. Does not change the history."""
    headlines = list(headlines or [])
    namespace = history_namespace(feed_or_key)
    hashes = {id(h): _sha(h.get(key)) for h in headlines if h.get(key) is not None}
    known = _known(namespace, list(set(hashes.values())))
    return [h for h in headlines if hashes.get(id(h)) not in known]


def filter_history_update(feed_or_key, headlines, key="link", keep=None, keep_days=None):
    """Drops headlines whose key is in the history, then records the keys of all given headlines.
    The history keeps the last `keep` keys (default 1000), or those seen in the last `keep_days` days.
    Available in Hy as `filter-history!`."""
    from feedxcavator.models import HistoryKey

    headlines = list(headlines or [])
    if not headlines:
        return []
    namespace = history_namespace(feed_or_key)
    hashes = {id(h): _sha(h.get(key)) for h in headlines if h.get(key) is not None}
    current = list(dict.fromkeys(hashes.values()))
    known = _known(namespace, current)
    result = [h for h in headlines if hashes.get(id(h)) not in known]

    now = timezone.now()
    try:
        with transaction.atomic():
            HistoryKey.objects.filter(namespace=namespace, key_hash__in=[h for h in current if h in known]).update(
                seen_at=now
            )
            HistoryKey.objects.bulk_create(
                # reversed, so that on a tie the items at the top of the source outlive the ones below
                [HistoryKey(namespace=namespace, key_hash=h, seen_at=now) for h in reversed(current)
                 if h not in known],
                ignore_conflicts=True,
            )
    except IntegrityError:
        pass
    prune_history(namespace, keep, keep_days)
    return result


def filter_history_by_guid_update(feed_or_key, headlines, keep=None, keep_days=None):
    """As filter_history_update, keyed by `guid`. Available in Hy as `filter-history-by-guid!`."""
    return filter_history_update(feed_or_key, headlines, "guid", keep, keep_days)


def prune_history(namespace: str, keep=None, keep_days=None):
    from feedxcavator.models import HistoryKey

    query = HistoryKey.objects.filter(namespace=namespace)
    if keep_days:
        query.filter(seen_at__lt=timezone.now() - timedelta(days=keep_days)).delete()
        if not keep:
            return
    keep = keep or DEFAULT_HISTORY_KEEP
    boundary = list(query.order_by("-seen_at", "-id").values_list("id", "seen_at")[keep:keep + 1])
    if boundary:
        stale = query.order_by("-seen_at", "-id").values_list("id", flat=True)[keep:]
        HistoryKey.objects.filter(id__in=list(stale)).delete()


# word filters -------------------------------------------------------------------


def _filter(name, create=False):
    from feedxcavator.models import WordFilter

    if create:
        return WordFilter.objects.get_or_create(name=name)[0]
    return WordFilter.objects.filter(name=name).first()


def _add(name, kind, value):
    from feedxcavator.models import WordFilterEntry

    WordFilterEntry.objects.get_or_create(filter=_filter(name, create=True), kind=kind, value=value)


def _remove(name, kind, value):
    from feedxcavator.models import WordFilterEntry

    WordFilterEntry.objects.filter(filter__name=name, kind=kind, value=value).delete()


def _args(args):
    """(word) or (word_filter, word), as in 3.0.0."""
    return ("default", args[0]) if len(args) == 1 else (args[0], args[1])


def add_filter_word(*args):
    """add_filter_word([word_filter,] word): adds a plain word to a word filter (`default` by default)."""
    name, word = _args(args)
    if word:
        _add(name, "word", str(word).lower())


def remove_filter_word(*args):
    """remove_filter_word([word_filter,] word)"""
    name, word = _args(args)
    if word:
        _remove(name, "word", str(word).lower())


def add_filter_regex(*args):
    """add_filter_regex([word_filter,] expr): adds a case-insensitive regular expression to a word filter."""
    name, expr = _args(args)
    if expr:
        re.compile(expr)
        _add(name, "regex", str(expr))


def remove_filter_regex(*args):
    """remove_filter_regex([word_filter,] expr)"""
    name, expr = _args(args)
    if expr:
        _remove(name, "regex", str(expr))


def list_word_filter(name="default") -> list[str]:
    """Entries of a word filter. Regular expressions are returned as their source text (WF-2)."""
    from feedxcavator.models import WordFilterEntry

    return list(WordFilterEntry.objects.filter(filter__name=name).order_by("id").values_list("value", flat=True))


def list_word_filters() -> list[str]:
    from feedxcavator.models import WordFilter

    return list(WordFilter.objects.order_by("name").values_list("name", flat=True))


# The console edits a filter as text, one entry per line. A line written as /expr/ is a regular
# expression, any other non-blank line a plain word.


def entry_line(kind, value) -> str:
    return f"/{value}/" if kind == "regex" else value


def filter_text(name) -> str | None:
    """The entries of a word filter as console text, or None when the filter does not exist."""
    word_filter = _filter(name)
    if word_filter is None:
        return None
    lines = [entry_line(k, v) for k, v in word_filter.entries.order_by("id").values_list("kind", "value")]
    return "\n".join(lines) + ("\n" if lines else "")


def parse_filter_text(text) -> tuple[list[tuple[str, str]], list[dict]]:
    """Entries (kind, value) from console text, without duplicates, and errors with their line numbers."""
    entries, errors, seen = [], [], set()
    for number, line in enumerate(str(text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if len(line) > 2 and line.startswith("/") and line.endswith("/"):
            entry = ("regex", line[1:-1])
            try:
                re.compile(entry[1])
            except re.error as e:
                errors.append({"line": number, "message": f"invalid regular expression: {e}"})
                continue
        else:
            entry = ("word", line.lower())
        if entry not in seen:
            seen.add(entry)
            entries.append(entry)
    return entries, errors


def replace_filter_entries(name, entries) -> None:
    """Makes `entries` the whole content of a word filter, creating it when needed, in their order."""
    from feedxcavator.models import WordFilterEntry

    with transaction.atomic():
        word_filter = _filter(name, create=True)
        word_filter.entries.all().delete()
        WordFilterEntry.objects.bulk_create(
            [WordFilterEntry(filter=word_filter, kind=kind, value=value) for kind, value in entries])


def load_matchers(name="default") -> list:
    from feedxcavator.models import WordFilterEntry

    matchers = []
    for kind, value in WordFilterEntry.objects.filter(filter__name=name).values_list("kind", "value"):
        if kind == "regex":
            try:
                matchers.append(re.compile(value, re.I | re.U))
            except re.error:
                continue
        else:
            matchers.append(value.lower())
    return matchers


def matches_filter(value, matchers) -> bool:
    """WF-1: a word matches when the lower-cased field contains it, a regex when it is found in it."""
    if not value or not matchers:
        return False
    value = str(value).lower()
    for matcher in matchers:
        if isinstance(matcher, str):
            if matcher in value:
                return True
        elif matcher.search(value):
            return True
    return False


def filter_content(feed, headlines, name=None, fields=None):
    """Drops headlines whose fields match a word filter. Without arguments the filter and the fields
    come from the `wordfilter` step of the feed."""
    headlines = list(headlines or [])
    if name is None:
        step = next((s["wordfilter"] for s in (feed.get("filter") or []) if s.get("wordfilter")), None)
        if step is None:
            return headlines
        name, fields = step.get("name", "default"), step.get("fields")
    matchers = load_matchers(name)
    fields = fields or ["title"]
    if not matchers:
        return headlines
    return [h for h in headlines if not any(matches_filter(h.get(f), matchers) for f in fields)]


def filter_headlines(feed, headlines, testing=None):
    """Runs the `filter` steps of a feed in order. A console test skips the history steps (FD-20)."""
    headlines = list(headlines or [])
    steps = feed.get("filter") if feed is not None else None
    if not steps:
        return headlines
    if testing is None:
        testing = context.run_mode.get() == "test"
    for step in steps:
        if step.get("history") is not None:
            if testing:
                continue
            opts = step["history"] if isinstance(step["history"], dict) else {}
            headlines = filter_history_update(
                feed, headlines, opts.get("key") or "link", opts.get("keep"), opts.get("keep_days")
            )
        elif step.get("dedupe"):
            seen, unique = set(), []
            for h in headlines:
                value = h.get(step["dedupe"])
                marker = repr(value)
                if value is None or marker not in seen:
                    seen.add(marker)
                    unique.append(h)
            headlines = unique
        elif step.get("wordfilter"):
            opts = step["wordfilter"]
            headlines = filter_content(feed, headlines, opts.get("name", "default"), opts.get("fields"))
        elif step.get("exclude"):
            opts = step["exclude"]
            regex = re.compile(opts["regex"])
            field = opts.get("field", "title")
            headlines = [h for h in headlines if not (h.get(field) and regex.search(str(h.get(field))))]
        elif step.get("limit") is not None:
            headlines = headlines[: step["limit"]]
    return headlines
