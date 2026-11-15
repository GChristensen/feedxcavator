"""MIG-1 to MIG-3: imports a 3.0.0 backup.edn."""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from feedxcavator import conf, feeddef, storage


def plain(value):
    """EDN data as plain Python: keywords become strings, sets and lists become lists."""
    import edn_format

    if isinstance(value, (edn_format.Keyword, edn_format.Symbol)):
        return value.name
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "items"):
        return {str(plain(k)): plain(v) for k, v in value.items()}
    if hasattr(value, "__iter__"):
        return [plain(v) for v in value]
    return str(value)


def slug(text) -> str:
    return re.sub(r"[^\w.-]+", "-", str(text).strip(), flags=re.UNICODE).strip("-.") or "feed"


def convert_definition(text: str, feed_id: str, params=None):
    """Rewrites a 3.0.0 definition into the new fields, keeping comments.
    Returns (yaml text, notes, group)."""
    notes = []
    tree = feeddef.load_yaml(text)
    if tree is None:
        tree = feeddef.load_yaml("{}\n")
    group = tree.pop("group", None)
    if "task" in tree:
        task = tree.pop("task")
        if task:
            tree["tasks"] = [task]
    realtime, partition = tree.pop("realtime", None), tree.pop("partition", None)
    if realtime or partition:
        websub = {"enabled": bool(realtime)}
        if partition:
            websub["partition"] = partition
        tree["websub"] = websub
    fetch = {}
    for key in ("charset", "proxy", "timeout"):
        if key in tree:
            value = tree.pop(key)
            if value:  # a timeout of 0 meant "no timeout"
                fetch[key] = value
    if fetch:
        tree["fetch"] = fetch
    if params is not None and "params" not in tree:
        tree["params"] = params
    for name, selector in (tree.get("selectors") or {}).items():
        if isinstance(selector, str) and (selector.lstrip()[:1] in ("[", "{") or selector.lstrip().startswith("#{")):
            notes.append(f"native Enlive selector in '{name}': {selector}")  # MIG-2
    if tree.get("output") == "edn":
        notes.append("output: edn is no longer supported (use json)")
    pages = tree.get("pages")
    if isinstance(pages, dict) and isinstance(pages.get("path"), str):
        pages["path"] = pages["path"].replace("%n", "{n}")
    tree.pop("id", None)
    if feed_id:  # --keep-ids: /feed/uuid:<id> URLs keep working
        tree.insert(0, "id", feed_id)
    return feeddef.dump_yaml(tree), notes, group


SCHEDULE = re.compile(r"\(\s*schedule(-weekly|-monthly)?\s+([^\s()]+)\s+(\d+)\s+(\d+)(?:\s+(\d+))?\s*\)")
DEFTASK = re.compile(
    r"\(\s*deftask\*\s+([^\s\[\]()]+)\s+\[([^\]]*)\](?:\s+:on-completion\s+(\[[^\]]*\]|[^\s()]+))?\s*\)")
DAYS = ["", "mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def unquote(name: str) -> str:
    return name.strip().strip('"')


def convert_tasks_module(code: str):
    """MIG-3: `schedule` declarations become schedules, `deftask*` declarations become tasks."""
    code = re.sub(r";[^\n]*", "", code)
    schedules = []
    for kind, task, hour, minute, day in SCHEDULE.findall(code):
        if kind == "-weekly" and day:
            cron = f"{int(minute)} {int(hour)} * * {DAYS[int(day) % 8] or '*'}"
        elif kind == "-monthly" and day:
            cron = f"{int(minute)} {int(hour)} {int(day)} * *"
        else:
            cron = f"{int(minute)} {int(hour)} * * *"
        entry = {"task": unquote(task), "cron": cron}
        if entry not in schedules:
            schedules.append(entry)
    tasks = {}
    for name, subtasks, then in DEFTASK.findall(code):
        task = {"run": [unquote(s) for s in subtasks.split()]}
        if then:
            task["then"] = [unquote(s) for s in then.strip("[]").split()]
        tasks[unquote(name)] = task
    return schedules, tasks


class Command(BaseCommand):
    help = "Imports a Feedxcavator 3.0.0 backup.edn into the workspace and the database."

    def add_arguments(self, parser):
        parser.add_argument("file", help="path to backup.edn")
        parser.add_argument("--force", action="store_true", help="overwrite feed directories that already exist")
        parser.add_argument("--keep-ids", action="store_true",
                            help="write the old UUID into each feed.yaml as `id`, so /feed/uuid:<id> URLs keep "
                                 "working; by default feeds are identified by their suffix")

    def handle(self, *args, **options):
        import edn_format
        import yaml

        from feedxcavator.models import KVObject, Settings, Subscription, WordFilter, WordFilterEntry

        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"{path} does not exist")
        data = plain(edn_format.loads(path.read_text(encoding="utf-8")))
        root = conf.workspace_dir()
        report = []

        # feeds
        params = {p.get("uuid"): p.get("params") for p in data.get("feed-params") or []}
        used, by_id, imported = set(), {}, 0
        for record in data.get("feed-definitions") or []:
            feed_id, text = record.get("uuid"), record.get("yaml") or ""
            if not feed_id:
                continue
            try:
                converted, notes, group = convert_definition(
                    text, feed_id if options["keep_ids"] else None, params.get(feed_id))
                suffix = (feeddef.plain(feeddef.load_yaml(converted)) or {}).get("suffix")
            except feeddef.DefinitionError as e:
                report.append(f"feed {feed_id}: not imported, {e}")
                continue
            name = slug(suffix or feed_id)
            if not suffix or not feeddef.SUFFIX_RE.fullmatch(str(suffix)):
                # the suffix is the identity now: give the feed a valid, explicit one
                valid = re.sub(r"[^A-Za-z0-9._~-]+", "-", str(suffix or "")).strip("-.") or f"feed-{feed_id[:8]}"
                if suffix:
                    notes.append(f"suffix '{suffix}' is not ASCII, changed to '{valid}': the feed URL changes")
                converted = feeddef.set_suffix(converted, valid)
                suffix, name = valid, valid
            group_path = "/".join(slug(part) for part in str(group or "").split("/") if part.strip())
            rel = f"{group_path}/{name}" if group_path else name
            n = 1
            while rel in used:
                n += 1
                rel = f"{group_path}/{name}-{n}" if group_path else f"{name}-{n}"
            used.add(rel)
            target = root / "feeds" / rel / "feed.yaml"
            if target.exists() and not options["force"]:
                report.append(f"feeds/{rel}: exists, skipped (use --force)")
                continue
            storage.write_atomic(target, converted)
            by_id[feed_id] = suffix
            imported += 1
            for note in notes:
                report.append(f"feeds/{rel}/feed.yaml: {note}")
            for diagnostic in feeddef.diagnose(converted):
                report.append(f"feeds/{rel}/feed.yaml: {diagnostic}")

        # code
        schedules, tasks = [], {}
        for record in data.get("code") or []:
            module, code = record.get("type"), record.get("code") or ""
            if not module:
                continue
            storage.write_atomic(root / "legacy" / f"{slug(module)}.clj", code)
            found_schedules, found_tasks = convert_tasks_module(code)
            schedules.extend(s for s in found_schedules if s not in schedules)
            tasks.update(found_tasks)
        if tasks and not self._has_entries(root / "tasks.yaml", "tasks"):
            storage.write_atomic(root / "tasks.yaml", yaml.safe_dump({"tasks": tasks}, sort_keys=False))
        if schedules and not self._has_entries(root / "schedules.yaml", "schedules"):
            storage.write_atomic(root / "schedules.yaml", yaml.safe_dump({"schedules": schedules}, sort_keys=False))

        # word filters: regular expressions are exported as strings that begin with a flags group
        words = 0
        for record in data.get("word-filters") or []:
            word_filter, _ = WordFilter.objects.get_or_create(name=record.get("id") or "default")
            for word in record.get("words") or []:
                m = re.match(r"\(\?[a-z]+\)", str(word))
                kind, value = ("regex", str(word)[m.end():]) if m else ("word", str(word).lower())
                WordFilterEntry.objects.get_or_create(filter=word_filter, kind=kind, value=value)
                words += 1

        # objects
        objects = 0
        for record in data.get("objects") or []:
            key = record.get("uuid")
            if key is None:
                continue
            value = record.get("object", record.get("value"))
            KVObject.objects.update_or_create(namespace="default", key=str(key), defaults={
                "value": json.loads(json.dumps(value, default=str)), "updated_at": timezone.now()})
            objects += 1

        # subscriptions
        suffix_to_id = {suffix: feed_id for feed_id, suffix in by_id.items() if suffix}
        subscriptions = 0
        for record in data.get("subscriptions") or []:
            topic, callback = record.get("topic") or "", record.get("callback")
            feed_id = record.get("uuid") or suffix_to_id.get(topic.split("/feed/")[-1])
            if feed_id and not options["keep_ids"]:
                feed_id = by_id.get(feed_id)  # subscriptions are kept under the suffix
            if not feed_id or not callback:
                report.append(f"subscription for {topic or '?'}: no matching feed, skipped")
                continue
            Subscription.objects.update_or_create(feed_id=feed_id, callback=callback, defaults={
                "topic": topic, "secret": record.get("secret") or "", "lease_seconds": 0, "expires_at": None,
                "verified": True, "created_at": timezone.now()})
            subscriptions += 1

        for record in (data.get("settings") or [])[:1]:
            settings = Settings.load()
            settings.enable_profiling = bool(record.get("enable-profiling"))
            settings.subscription_url = record.get("subscription-url") or ""
            settings.save()

        self.stdout.write(
            f"Imported {imported} feeds, {words} word filter entries, {objects} objects, "
            f"{subscriptions} subscriptions, {len(schedules)} schedules, {len(tasks)} tasks.\n"
            f"Code modules were written to {root / 'legacy'} for manual porting; they are not loaded.\n"
            "The API token and the password are not imported: generate a token on the Settings tab and run "
            "`feedxcavator set-password`.")
        if report:
            self.stdout.write("\nNeeds attention:")
            for line in report:
                self.stdout.write(f"  - {line}")

    @staticmethod
    def _has_entries(path: Path, key: str) -> bool:
        import yaml

        if not path.exists():
            return False
        try:
            return bool((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(key))
        except yaml.YAMLError:
            return True
