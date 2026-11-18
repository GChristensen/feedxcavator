"""The WebSub hub."""

from __future__ import annotations

import hashlib
import hmac
import threading
from datetime import timedelta
from urllib.parse import urlencode

from django.utils import timezone

from feedxcavator import applog, conf, context, fetch, output, storage

DEFAULT_LEASE_SECONDS = 10 * 24 * 3600


def enabled() -> bool:
    return conf.get("websub", True) is not False


def feed_from_topic(topic: str):
    """Resolves /feed/<suffix> and /feed/uuid:<id>."""
    from feedxcavator import workspace

    if not topic or "/feed/" not in topic:
        return None
    suffix = topic.split("/feed/")[-1].split("?")[0].strip("/")
    registry = workspace.get_registry()
    if suffix.startswith("uuid:"):
        return registry.feed_by_id(suffix[5:])
    return registry.feed_by_suffix(suffix)


def _with_params(url: str, params: dict) -> str:
    """Appends parameters with `?` or `&` as needed."""
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def verify(callback: str, mode: str, topic: str, lease_seconds: int | None) -> bool:
    """Sends the verification GET and checks that the subscriber echoes the challenge."""
    challenge = storage.new_id()
    params = {"hub.mode": mode, "hub.topic": topic, "hub.challenge": challenge}
    if lease_seconds:
        params["hub.lease_seconds"] = lease_seconds
    body = fetch.fetch_url(_with_params(callback, params), proxy=False, insecure=True, as_="string", retry=False)
    return body is not None and body.strip() == challenge


def _verify_subscription(subscription_id: int):
    from feedxcavator.models import Subscription

    with context.scope(source="websub"):
        try:
            context.new_fetch_state()
            sub = Subscription.objects.filter(id=subscription_id).first()
            if sub is None:
                return
            if verify(sub.callback, "subscribe", sub.topic, sub.lease_seconds):
                Subscription.objects.filter(id=sub.id).update(verified=True)
            else:
                applog.write("warning", f"WebSub verification failed for {sub.callback} ({sub.topic})")
                Subscription.objects.filter(id=sub.id, verified=False).delete()
        except Exception as e:  # noqa: BLE001
            applog.write("error", e)
        finally:
            from django.db import connection

            connection.close()


def _in_background(target, *args):
    threading.Thread(target=target, args=args, daemon=True, name="websub").start()


def subscribe(params, background=True) -> int:
    from feedxcavator.models import Subscription

    topic, callback = params.get("hub.topic"), params.get("hub.callback")
    feed = feed_from_topic(topic)
    if feed is None or not callback:
        return 404
    try:
        lease = int(params.get("hub.lease_seconds") or DEFAULT_LEASE_SECONDS)
    except ValueError:
        lease = DEFAULT_LEASE_SECONDS
    now = timezone.now()
    sub, created = Subscription.objects.get_or_create(
        feed_id=feed["key"], callback=callback,
        defaults={"topic": topic, "secret": params.get("hub.secret") or "", "lease_seconds": lease,
                  "expires_at": now + timedelta(seconds=lease), "created_at": now},
    )
    if not created:
        sub.topic = topic
        sub.secret = params.get("hub.secret") or ""
        sub.lease_seconds = lease
        sub.expires_at = now + timedelta(seconds=lease)
        sub.save()
    if background:
        _in_background(_verify_subscription, sub.id)
    else:
        _verify_subscription(sub.id)
    return 202


def _verify_unsubscription(callback, topic):
    with context.scope(source="websub"):
        context.new_fetch_state()
        verify(callback, "unsubscribe", topic, None)


def unsubscribe(params, background=True) -> int:
    from feedxcavator.models import Subscription

    topic, callback = params.get("hub.topic"), params.get("hub.callback")
    feed = feed_from_topic(topic)
    if feed is None:
        return 404
    query = Subscription.objects.filter(feed_id=feed["key"])
    if callback:
        query = query.filter(callback=callback)
    query.delete()
    if callback:
        if background:
            _in_background(_verify_unsubscription, callback, topic)
        else:
            _verify_unsubscription(callback, topic)
    return 202


def publish_content(feed, result: dict) -> int:
    """POSTs a feed document to every verified subscriber whose lease has not expired.
    Returns the number of successful deliveries."""
    from feedxcavator.models import Subscription

    if not enabled():
        return 0
    now = timezone.now()
    Subscription.objects.filter(feed_id=feed["key"], expires_at__lt=now).delete()
    feed_url = output.get_feed_url(feed)
    body = result["output"].encode("utf-8")
    delivered = 0
    for sub in Subscription.objects.filter(feed_id=feed["key"], verified=True):
        headers = {
            "Content-Type": f"{result['content_type']}; charset=utf-8",
            "Link": f'<{feed_url}>; rel="self", <{output.get_websub_url()}>; rel="hub"',
        }
        if sub.secret:
            signature = hmac.new(sub.secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
            headers["X-Hub-Signature"] = f"sha1={signature}"
        callback = sub.callback
        if "feedly.com" in callback:
            callback = _with_params(callback, {"hub.mode": "publish"})
        response = fetch.fetch_url(callback, method="POST", insecure=True, proxy=False, headers=headers,
                                   payload=body)
        if response is None:
            with context.scope(source="websub"):
                error = fetch.get_last_network_error()
                if error is not None:
                    applog.write("error", f"WebSub delivery to {callback} failed: {error!r}")
                else:
                    failed = fetch.get_last_http_response()
                    applog.write(
                        "error",
                        f"HTTP error during WebSub publishing: {fetch.get_last_http_error()}\n"
                        f"response content: {failed.text[:2000] if failed else ''}\nHTTP response: {failed!r}",
                    )
        else:
            delivered += 1
    return delivered


def publish(params) -> int:
    """hub.mode=publish: pushes the stored output of the feed."""
    feed = feed_from_topic(params.get("hub.url") or params.get("hub.topic"))
    if feed is None:
        return 404
    stored = storage.fetch_feed_output(feed["key"])
    if stored:
        publish_content(feed, stored)
    return 204


def hub_action(params) -> int:
    """Dispatches a hub request and returns the HTTP status."""
    if not enabled():
        return 404
    mode = params.get("hub.mode")
    if mode == "subscribe":
        return subscribe(params)
    if mode == "unsubscribe":
        return unsubscribe(params)
    if mode == "publish":
        return publish(params)
    return 400


def delete_feed_subscriptions(feed_id: str):
    """Deletes the subscriptions of a feed."""
    from feedxcavator.models import Subscription

    Subscription.objects.filter(feed_id=feed_id).delete()
