"""Payload files, blobs, stored feed output and the key-value store."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from feedxcavator import conf

_UNSAFE = re.compile(r"[^\w\-./\\]", re.UNICODE)
TMP_SUFFIX = re.compile(r"\.[0-9a-f]{32}\.tmp$")


def sanitize_path(path: str) -> str:
    """Everything but letters, digits, `-`, `_`, `.`, `/` and `\\` becomes `_`."""
    return _UNSAFE.sub("_", path)


def new_id() -> str:
    return uuid.uuid4().hex


def write_atomic(path: Path, data: bytes | str):
    """Writes to `<name>.<32 hex>.tmp` in the same directory, then moves it into place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


@contextmanager
def file_lock(name: str):
    """A cross-process lock backed by a file in the data directory."""
    conf.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = conf.DATA_DIR / f"{name}.lock"
    f = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        finally:
            f.close()


# feed output ----------------------------------------------------------------


def feed_output_path(feed_id: str) -> Path:
    return conf.FILES_DIR / "feed" / sanitize_path(feed_id)


def store_feed_output(feed_id: str, content: str | bytes, content_type: str, item_count: int = 0):
    from feedxcavator.models import FeedOutput

    write_atomic(feed_output_path(feed_id), content)
    FeedOutput.objects.update_or_create(
        feed_id=feed_id,
        defaults={"content_type": content_type, "generated_at": timezone.now(), "item_count": item_count},
    )


def fetch_feed_output(feed_id: str) -> dict | None:
    """Returns {"output": str, "content_type": str} or None."""
    from feedxcavator.models import FeedOutput

    record = FeedOutput.objects.filter(feed_id=feed_id).first()
    path = feed_output_path(feed_id)
    if not record or not path.exists():
        return None
    return {
        "output": path.read_text(encoding="utf-8"),
        "content_type": record.content_type,
        "generated_at": record.generated_at,
        "item_count": record.item_count,
    }


def delete_feed_output(feed_id: str):
    from feedxcavator.models import FeedOutput

    FeedOutput.objects.filter(feed_id=feed_id).delete()
    feed_output_path(feed_id).unlink(missing_ok=True)


def rename_feed_state(old_key: str, new_key: str) -> dict:
    """Moves the stored state of a feed (output, history, subscriptions, run records) to another key.
    State left under the new key by a feed that no longer exists is dropped first."""
    from feedxcavator.models import FeedOutput, FeedRun, HistoryKey, Subscription

    def namespace(key):
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    counts = {}
    with transaction.atomic():
        FeedOutput.objects.filter(feed_id=new_key).delete()
        Subscription.objects.filter(feed_id=new_key).delete()
        HistoryKey.objects.filter(namespace=namespace(new_key)).delete()
        record = FeedOutput.objects.filter(feed_id=old_key).first()
        if record is not None:
            FeedOutput.objects.create(feed_id=new_key, content_type=record.content_type,
                                      generated_at=record.generated_at, item_count=record.item_count)
            record.delete()
        counts["history"] = HistoryKey.objects.filter(namespace=namespace(old_key)).update(
            namespace=namespace(new_key))
        counts["subscriptions"] = Subscription.objects.filter(feed_id=old_key).update(feed_id=new_key)
        counts["runs"] = FeedRun.objects.filter(feed_id=old_key).update(feed_id=new_key)
    old_path, new_path = feed_output_path(old_key), feed_output_path(new_key)
    counts["output"] = old_path.exists()
    if old_path.exists():
        os.replace(old_path, new_path)
    else:
        new_path.unlink(missing_ok=True)
    return counts


# blobs ------------------------------------------------------------------------


def blob_path(sha256: str) -> Path:
    return conf.FILES_DIR / "blob" / sha256


def store_blob(data: bytes, content_type: str = "", source_url: str = "", blob_id: str | None = None):
    """The file is written before its record. Identical content is stored once."""
    from feedxcavator.models import Blob

    digest = hashlib.sha256(data).hexdigest()
    path = blob_path(digest)
    if not path.exists():
        write_atomic(path, data)
    return Blob.objects.create(
        id=blob_id or new_id(),
        sha256=digest,
        content_type=content_type or "",
        size=len(data),
        source_url=source_url or "",
        created_at=timezone.now(),
    )


def find_blob(blob_id: str):
    from feedxcavator.models import Blob

    blob = Blob.objects.filter(id=blob_id).first()
    if blob is None and "." in blob_id:  # 3.0.0 image names carried an extension
        blob = Blob.objects.filter(id=blob_id.rsplit(".", 1)[0]).first()
    return blob


def find_blob_by_source(source_url: str):
    from feedxcavator.models import Blob

    return Blob.objects.filter(source_url=source_url).order_by("-created_at").first() if source_url else None


def delete_blob(blob) -> None:
    """Deletes the record, and the file when no other record shares its content."""
    from feedxcavator.models import Blob

    with transaction.atomic():
        digest = blob.sha256
        blob.delete()
        shared = Blob.objects.filter(sha256=digest).exists()
    if not shared:
        blob_path(digest).unlink(missing_ok=True)


def delete_all_blobs() -> int:
    from feedxcavator.models import Blob

    count = 0
    for blob in list(Blob.objects.all()):
        delete_blob(blob)
        count += 1
    return count


# key-value objects ----------------------------------------------------------------


def store_object(key: str, value, namespace: str = "default", ttl: float | None = None):
    """Stores a JSON-serializable value under a string key. `ttl` is a lifetime in seconds."""
    from feedxcavator.models import KVObject

    now = timezone.now()
    KVObject.objects.update_or_create(
        namespace=namespace,
        key=str(key),
        defaults={"value": value, "updated_at": now, "expires_at": now + timedelta(seconds=ttl) if ttl else None},
    )
    return value


def fetch_object(key: str, default=None, namespace: str = "default"):
    """Returns the value stored under the key, or `default`."""
    from feedxcavator.models import KVObject

    record = KVObject.objects.filter(namespace=namespace, key=str(key)).first()
    if record is None or (record.expires_at and record.expires_at <= timezone.now()):
        return default
    return record.value


def delete_object(key: str, namespace: str = "default"):
    """Deletes the value stored under the key."""
    from feedxcavator.models import KVObject

    KVObject.objects.filter(namespace=namespace, key=str(key)).delete()
