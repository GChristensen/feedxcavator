"""Database models (PORT-REQUIREMENTS section 14)."""

from django.db import models


class FeedIndex(models.Model):
    """STO2-2. A cache of the parsed feeds, rebuilt from the workspace on each load."""

    id = models.CharField(primary_key=True, max_length=255)  # the feed key: its id, or its suffix
    suffix = models.CharField(max_length=255, unique=True, null=True)
    path = models.CharField(max_length=1024)
    title = models.CharField(max_length=1024, blank=True, default="")
    tasks = models.JSONField(default=list)
    tags = models.JSONField(default=list)
    enabled = models.BooleanField(default=True)
    status = models.CharField(max_length=8, default="ok")  # ok | error
    error = models.TextField(blank=True, default="")
    definition = models.JSONField(default=dict)
    mtime = models.FloatField(default=0)


class FeedOutput(models.Model):
    """STO2-3. The document itself is the file data/files/feed/<feed-id> (OUT-6)."""

    feed_id = models.CharField(primary_key=True, max_length=255)
    content_type = models.CharField(max_length=128)
    generated_at = models.DateTimeField()
    item_count = models.IntegerField(default=0)


class HistoryKey(models.Model):
    """STO2-4."""

    namespace = models.CharField(max_length=64)
    key_hash = models.CharField(max_length=64)
    seen_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["namespace", "key_hash"], name="history_ns_key")]
        indexes = [models.Index(fields=["namespace", "seen_at"], name="history_ns_seen")]


class TaskRun(models.Model):
    """STO2-6."""

    task = models.CharField(max_length=255, db_index=True)
    trigger = models.CharField(max_length=16)  # schedule | manual | then | subtask
    parent = models.ForeignKey("self", null=True, on_delete=models.CASCADE, related_name="children")
    started_at = models.DateTimeField(db_index=True)
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=16)  # running | ok | failed | skipped | timeout
    error = models.TextField(blank=True, default="")


class FeedRun(models.Model):
    task_run = models.ForeignKey(TaskRun, on_delete=models.CASCADE, related_name="feed_runs")
    feed_id = models.CharField(max_length=255, db_index=True)
    feed_title = models.CharField(max_length=1024, blank=True, default="")
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=16)  # running | ok | error | timeout
    headline_count = models.IntegerField(null=True)
    http_status = models.IntegerField(null=True)
    error = models.TextField(blank=True, default="")
    expect_empty = models.BooleanField(default=False)


class ScheduleState(models.Model):
    """STO2-7. `key` is task|cron|timezone."""

    key = models.CharField(primary_key=True, max_length=512)
    first_seen_at = models.DateTimeField()
    last_fired_at = models.DateTimeField(null=True)


class Subscription(models.Model):
    """STO2-8."""

    feed_id = models.CharField(max_length=255, db_index=True)
    topic = models.CharField(max_length=2048)
    callback = models.CharField(max_length=2048)
    secret = models.CharField(max_length=512, blank=True, default="")
    lease_seconds = models.IntegerField(default=0)
    expires_at = models.DateTimeField(null=True)
    verified = models.BooleanField(default=False)
    created_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["feed_id", "callback"], name="subscription_feed_callback")]


class WordFilter(models.Model):
    """STO2-9."""

    name = models.CharField(max_length=255, unique=True)


class WordFilterEntry(models.Model):
    filter = models.ForeignKey(WordFilter, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=8)  # word | regex
    value = models.TextField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["filter", "kind", "value"], name="wordfilter_entry")]


class KVObject(models.Model):
    """STO2-10."""

    namespace = models.CharField(max_length=255, default="default")
    key = models.CharField(max_length=1024)
    value = models.JSONField(null=True)
    updated_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["namespace", "key"], name="kv_ns_key")]


class Blob(models.Model):
    """STO2-11. The file is data/files/blob/<sha256>."""

    id = models.CharField(primary_key=True, max_length=64)
    sha256 = models.CharField(max_length=64, db_index=True)
    content_type = models.CharField(max_length=128, blank=True, default="")
    size = models.BigIntegerField(default=0)
    source_url = models.CharField(max_length=2048, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(db_index=True)


class LogEntry(models.Model):
    """STO2-12. `id` is the sequence number of LOG-2, `timestamp` is in milliseconds."""

    level = models.CharField(max_length=8, db_index=True)
    source = models.CharField(max_length=255, blank=True, default="", db_index=True)
    timestamp = models.BigIntegerField(db_index=True)
    message = models.TextField()
    task_run = models.ForeignKey(TaskRun, null=True, on_delete=models.SET_NULL)


class Settings(models.Model):
    """STO2-13. A single row with id 1."""

    enable_profiling = models.BooleanField(default=False)
    subscription_url = models.CharField(max_length=2048, blank=True, default="")

    @classmethod
    def load(cls) -> "Settings":
        return cls.objects.get_or_create(id=1)[0]


class ApiToken(models.Model):
    """STO2-14. Only the SHA-256 of the token is stored."""

    token_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField()


class QueueItem(models.Model):
    """Requests from the web process to the worker (PRC-3)."""

    kind = models.CharField(max_length=32)  # task | reschedule | service-task
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField()
