"""Telegram support on Telethon (TG-1 to TG-7).

The client runs in the worker and is connected per call, as in 3.0.0. Calls are serialized. Calls made
in the web process (a console test) are forwarded to the worker.
"""

from __future__ import annotations

import asyncio
import html as _html
import re
import threading

from feedxcavator import applog, conf, context, storage

MESSAGE_LIMIT = 26
_lock = threading.Lock()
in_worker = False  # set by the worker at start


def session_path():
    return conf.DATA_DIR / "telegram" / "feedxcavator"


def configured() -> bool:
    return bool(conf.telegram("api-id") and conf.telegram("api-hash"))


def _inactive_reason() -> str | None:
    if not configured():
        return "Telegram is not configured: set telegram.api-id and telegram.api-hash in config.yml"
    if not session_path().with_suffix(".session").exists():
        return "Telegram has no session: run `feedxcavator telegram-login`"
    return None


def proxy_name(proxy=None):
    """TG2-1: the proxy a call uses, resolved in the calling process. An explicit name wins and False means a
    direct connection; otherwise the `fetch.proxy` of the current feed, and failing that the proxy named
    `default` when `telegram.default-proxy` is on."""
    if proxy is False:
        return False
    if proxy:
        return str(getattr(proxy, "name", proxy))
    feed = context.current_feed.get()
    feed_proxy = ((feed.get("fetch") if feed is not None else None) or {}).get("proxy")
    if feed_proxy:
        return str(feed_proxy)
    return "default" if conf.telegram("default-proxy") else None


def _proxy(name=None):
    """The Telethon proxy settings for a proxy name, None for a direct connection."""
    from feedxcavator import fetch

    proxy = fetch.resolve_proxy(name) if name else None
    if not proxy:
        return None
    schemes = {"socks5": "socks5", "socks5h": "socks5", "socks4": "socks4", "socks4a": "socks4"}
    return {
        "proxy_type": schemes.get(str(proxy.get("scheme")).lower(), "http"),
        "addr": proxy["host"], "port": int(proxy["port"]), "rdns": True,
        "username": proxy.get("user") or None, "password": proxy.get("password") or None,
    }


def make_client(proxy=None):
    from telethon import TelegramClient

    session_path().parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(session_path()), int(conf.telegram("api-id")), str(conf.telegram("api-hash")),
                          proxy=_proxy(proxy), device_model="PC", app_version="4", system_lang_code="en")


# rendering ------------------------------------------------------------------------------------

_BARE_URL = re.compile(r"(?<![\](<\"'=])(https?://[^\s<>)\]]+)")


def render_text(text: str) -> str:
    """Markdown to HTML. Bare URLs become links and a leading `#` on a word becomes `_` (INT-3)."""
    import markdown

    text = re.sub(r"(^|\s)#", r"\1_", text or "")
    text = _BARE_URL.sub(r"<\1>", text)
    try:
        return markdown.markdown(text, extensions=["nl2br"])
    except Exception as e:  # noqa: BLE001
        applog.write("warning", f"Markdown parsing failed: {e}")
        return f"<p>{_html.escape(text, quote=False)}</p>"


def group_albums(messages: list) -> list[list]:
    """Messages that share an album id become one group. Service messages are skipped."""
    groups: list[list] = []
    by_album: dict = {}
    for message in messages:
        if getattr(message, "action", None) is not None:  # pins, title and photo changes
            continue
        album = getattr(message, "grouped_id", None)
        if album and album in by_album:
            by_album[album].append(message)
            continue
        group = [message]
        if album:
            by_album[album] = group
        groups.append(group)
    return [sorted(group, key=lambda m: m.id) for group in groups]


def headline_from(channel: str, group: list, image_urls: list[str]) -> dict | None:
    text = "".join(getattr(m, "text", None) or "" for m in group)
    with_text = next((m for m in group if getattr(m, "text", None)), group[0])
    if not text.strip() and not image_urls:
        return None
    thumbs = "<br>\n".join(f"<img src='{url}'>" for url in image_urls)
    return {
        "link": f"https://t.me/{channel.lstrip('@')}/{with_text.id}",
        "guid": f"tg:{channel.lstrip('@')}:{group[0].id}",
        "summary": f"{render_text(text)}<br>\n{thumbs}",
        "published": getattr(group[0], "date", None),
    }


async def _store_media(client, channel: str, message) -> str | None:
    """Photos and video thumbnails go to the blob store. A file stored for the same message is reused."""
    has_photo = getattr(message, "photo", None) is not None
    has_video = getattr(message, "video", None) is not None
    web_photo = getattr(getattr(getattr(message, "media", None), "webpage", None), "photo", None)
    if not (has_photo or has_video or web_photo):
        return None
    source = f"tg://{channel.lstrip('@')}/{message.id}"
    blob = await asyncio.to_thread(storage.find_blob_by_source, source)
    if blob is None:
        try:
            data = await client.download_media(message, bytes, thumb=-1 if has_video and not has_photo else None)
        except Exception as e:  # noqa: BLE001
            applog.write("error", f"Error downloading Telegram media of {source}: {e}")
            return None
        if not data:
            return None
        blob = await asyncio.to_thread(storage.store_blob, data, "image/jpeg", source)
    host = context.request_host.get() or conf.app_host() or ""
    return f"{host}/image/{blob.id}"


async def _get_channel_messages(channel: str, limit: int, proxy=None) -> list[dict]:
    client = make_client(proxy)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            applog.write("warning", "The Telegram session is not authorized: run `feedxcavator telegram-login`")
            return []
        messages = await client.get_messages(channel, limit=limit)
        headlines = []
        for group in group_albums(list(messages)):
            images = []
            for message in group:
                url = await _store_media(client, channel, message)
                if url:
                    images.append(url)
            headline = headline_from(channel, group, images)
            if headline:
                headlines.append(headline)
        return headlines
    finally:
        await client.disconnect()


async def _notify(message: str, chat_id, proxy=None) -> bool:
    client = make_client(proxy)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return False
        await client.send_message(chat_id, message)
        return True
    finally:
        await client.disconnect()


def _run(coroutine):
    with _lock:  # TG-3: calls are serialized
        try:
            return asyncio.run(coroutine)
        finally:
            from django.db import connections

            connections.close_all()


def _local_get_channel_messages(channel, limit=MESSAGE_LIMIT, proxy=None):
    reason = _inactive_reason()
    if reason:
        applog.write("warning", reason)  # TG-5
        return []
    try:
        return _run(_get_channel_messages(str(channel), min(int(limit or MESSAGE_LIMIT), MESSAGE_LIMIT), proxy))
    except Exception as e:  # noqa: BLE001
        applog.write("error", f"Telegram error: {e!r}")
        return []


def _local_notify(message, chat_id, proxy=None):
    reason = _inactive_reason()
    if reason:
        applog.write("warning", reason)
        return None
    try:
        return _run(_notify(str(message), chat_id, proxy))
    except Exception as e:  # noqa: BLE001
        applog.write("error", f"Telegram error: {e!r}")
        return None


def _forward(method, *args, **kwargs):
    from feedxcavator import rpc

    try:
        return rpc.call(method, *args, **kwargs)
    except OSError as e:
        applog.write("warning", f"Telegram runs in the worker process, which is not reachable: {e}")
        return [] if method.endswith("messages") else None


def get_channel_messages(channel, limit=MESSAGE_LIMIT, proxy=None):
    proxy = proxy_name(proxy)
    if in_worker:
        return _local_get_channel_messages(channel, limit, proxy=proxy)
    return _forward("telegram_get_channel_messages", channel, limit, proxy=proxy)


def notify(message, chat_id, proxy=None):
    proxy = proxy_name(proxy)
    if in_worker:
        return _local_notify(message, chat_id, proxy=proxy)
    return _forward("telegram_notify", message, chat_id, proxy=proxy)


def login():
    """TG-6: the interactive login (phone number and code) that creates the session."""
    if not configured():
        raise SystemExit("Set telegram.api-id and telegram.api-hash in config.yml first.")
    client = make_client(proxy_name())
    client.start()
    me = client.loop.run_until_complete(client.get_me())
    print(f"Logged in as {getattr(me, 'username', None) or getattr(me, 'first_name', '')}. "
          f"Session stored in {session_path()}.session")
    client.disconnect()
