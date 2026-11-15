"""The maintenance task (MNT-1, STO2-16)."""

from __future__ import annotations

import time
from datetime import timedelta

from django.utils import timezone

from feedxcavator import applog, context, filters, runner, storage, workspace

MAX_AGE_HOURS = 48


def service_task() -> dict:
    """Deletes images and log entries older than 48 hours, and prunes history, run records, expired
    objects and expired subscriptions."""
    from feedxcavator.models import Blob, HistoryKey, KVObject, LogEntry, Subscription

    with context.scope(source="service-task"):
        started = time.monotonic()
        counts = {}
        try:
            now = timezone.now()
            threshold = now - timedelta(hours=MAX_AGE_HOURS)
            old = list(Blob.objects.filter(created_at__lt=threshold))
            failed = 0
            for blob in old:
                try:
                    storage.delete_blob(blob)
                except Exception as e:  # noqa: BLE001 - one failure does not stop the task
                    failed += 1
                    applog.write("error", f"error deleting image {blob.id}: {e}")
            counts["images"] = len(old) - failed
            counts["log_entries"], _ = LogEntry.objects.filter(
                timestamp__lt=int((time.time() - MAX_AGE_HOURS * 3600) * 1000)).delete()
            counts["runs"] = runner.prune_runs()
            counts["objects"], _ = KVObject.objects.filter(expires_at__lt=now).delete()
            counts["subscriptions"], _ = Subscription.objects.filter(expires_at__lt=now).delete()

            history = 0
            for feed in workspace.get_registry().active_feeds():
                for step in feed.get("filter") or []:
                    options = step.get("history")
                    if isinstance(options, dict) and options.get("keep_days"):
                        deleted, _ = HistoryKey.objects.filter(
                            namespace=filters.history_namespace(feed),
                            seen_at__lt=now - timedelta(days=options["keep_days"])).delete()
                        history += deleted
            counts["history_keys"] = history
            applog.write(
                "info",
                f"service task completed in {int((time.monotonic() - started) * 1000)} ms: purged "
                f"{counts['images']} of {len(old)} images and {counts['log_entries']} log entries older than "
                f"2 days, {counts['runs']} run records, {counts['history_keys']} history keys, "
                f"{counts['objects']} expired objects, {counts['subscriptions']} expired subscriptions",
            )
        except Exception as e:  # noqa: BLE001
            applog.write("error", e)
        return counts
