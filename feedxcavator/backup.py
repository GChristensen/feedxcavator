"""Backup and restore (STO2-15): a zip with one JSON file per model and, optionally, the workspace."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from datetime import datetime

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from feedxcavator import conf, workspace


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def export_data() -> dict:
    from feedxcavator.models import KVObject, ScheduleState, Settings, Subscription, WordFilter

    settings = Settings.load()
    return {
        "wordfilters": [
            {"name": f.name, "entries": [{"kind": e.kind, "value": e.value} for e in f.entries.order_by("id")]}
            for f in WordFilter.objects.order_by("name")
        ],
        "kvobjects": [
            {"namespace": o.namespace, "key": o.key, "value": o.value, "updated_at": _iso(o.updated_at),
             "expires_at": _iso(o.expires_at)}
            for o in KVObject.objects.order_by("namespace", "key")
        ],
        "subscriptions": [
            {"feed_id": s.feed_id, "topic": s.topic, "callback": s.callback, "secret": s.secret,
             "lease_seconds": s.lease_seconds, "expires_at": _iso(s.expires_at), "verified": s.verified,
             "created_at": _iso(s.created_at)}
            for s in Subscription.objects.order_by("id")
        ],
        "settings": [{"subscription_url": settings.subscription_url}],
        "schedulestate": [
            {"key": s.key, "first_seen_at": _iso(s.first_seen_at), "last_fired_at": _iso(s.last_fired_at)}
            for s in ScheduleState.objects.order_by("key")
        ],
    }


def workspace_tarball() -> bytes:
    buffer = io.BytesIO()
    root = conf.workspace_dir()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if set(rel.parts) & (workspace.IGNORED_DIRS - {"legacy"}) or path.name.endswith(".tmp"):
                continue
            if path.is_file():
                tar.add(path, arcname=rel.as_posix())
    return buffer.getvalue()


def create_backup(include_workspace: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({"format": 1, "created_at": timezone.now().isoformat()}))
        for name, records in export_data().items():
            archive.writestr(f"{name}.json", json.dumps(records, ensure_ascii=False, indent=1))
        if include_workspace:
            archive.writestr("workspace.tar.gz", workspace_tarball())
    return buffer.getvalue()


def _dt(value):
    return parse_datetime(value) if value else None


def import_data(data: dict) -> dict:
    """Upserts the records of a backup."""
    from feedxcavator.models import (KVObject, ScheduleState, Settings, Subscription, WordFilter,
                                     WordFilterEntry)

    counts = {}
    now = timezone.now()
    with transaction.atomic():
        for record in data.get("wordfilters") or []:
            word_filter, _ = WordFilter.objects.get_or_create(name=record["name"])
            for entry in record.get("entries") or []:
                WordFilterEntry.objects.get_or_create(filter=word_filter, kind=entry["kind"], value=entry["value"])
        counts["wordfilters"] = len(data.get("wordfilters") or [])
        for record in data.get("kvobjects") or []:
            KVObject.objects.update_or_create(
                namespace=record.get("namespace") or "default", key=record["key"],
                defaults={"value": record.get("value"), "updated_at": _dt(record.get("updated_at")) or now,
                          "expires_at": _dt(record.get("expires_at"))})
        counts["kvobjects"] = len(data.get("kvobjects") or [])
        for record in data.get("subscriptions") or []:
            Subscription.objects.update_or_create(
                feed_id=record["feed_id"], callback=record["callback"],
                defaults={"topic": record.get("topic") or "", "secret": record.get("secret") or "",
                          "lease_seconds": record.get("lease_seconds") or 0,
                          "expires_at": _dt(record.get("expires_at")), "verified": record.get("verified", True),
                          "created_at": _dt(record.get("created_at")) or now})
        counts["subscriptions"] = len(data.get("subscriptions") or [])
        for record in (data.get("settings") or [])[:1]:
            settings = Settings.load()
            settings.subscription_url = record.get("subscription_url") or ""
            settings.save()
        counts["settings"] = min(1, len(data.get("settings") or []))
        for record in data.get("schedulestate") or []:
            ScheduleState.objects.update_or_create(
                key=record["key"], defaults={"first_seen_at": _dt(record.get("first_seen_at")) or now,
                                             "last_fired_at": _dt(record.get("last_fired_at"))})
        counts["schedulestate"] = len(data.get("schedulestate") or [])
    return counts


def workspace_is_empty() -> bool:
    root = conf.workspace_dir()
    if not root.exists():
        return True
    for path in root.rglob("*"):
        if path.is_file() and path.name not in ("tasks.yaml", "schedules.yaml", "scratch.hy", "scratch.py"):
            return False
    return True


def restore_backup(content: bytes, unpack_workspace: bool = False) -> dict:
    """Restores a backup zip. The workspace of the archive is unpacked only into an empty workspace."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        data = {name[:-5]: json.loads(archive.read(name)) for name in names
                if name.endswith(".json") and name != "manifest.json"}
        result = {"records": import_data(data), "has_workspace": "workspace.tar.gz" in names,
                  "workspace_unpacked": False, "workspace_empty": workspace_is_empty()}
        if unpack_workspace and result["has_workspace"]:
            if not result["workspace_empty"]:
                result["workspace_error"] = "the workspace directory is not empty"
            else:
                root = conf.workspace_dir()
                root.mkdir(parents=True, exist_ok=True)
                with tarfile.open(fileobj=io.BytesIO(archive.read("workspace.tar.gz")), mode="r:gz") as tar:
                    tar.extractall(root, filter="data")
                result["workspace_unpacked"] = True
    return result
