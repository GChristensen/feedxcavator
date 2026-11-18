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
    """TG2-1: the proxy a call uses, resolved in the calling process. `telegram.use-proxy`, when set, is used
    for every call. Otherwise an explicit name, with False for a direct connection; otherwise the `fetch.proxy`
    of the current feed; otherwise a direct connection."""
    forced = conf.telegram("use-proxy")
    if forced:
        return str(forced)
    if proxy is False:
        return False
    if proxy:
        return str(getattr(proxy, "name", proxy))
    feed = context.current_feed.get()
    feed_proxy = ((feed.get("fetch") if feed is not None else None) or {}).get("proxy")
    return str(feed_proxy) if feed_proxy else None


def _mtproto_secret(secret) -> tuple[str, bool]:
    """The 16-byte key of an MTProto proxy secret (hex or base64) as hex, and whether it is a dd-secret (which
    needs the randomized intermediate protocol). Fake-TLS ee-secrets are refused: Telethon cannot speak it."""
    import base64
    import binascii

    text = str(secret or "").strip()
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        try:
            raw = base64.urlsafe_b64decode(text.replace("+", "-").replace("/", "_") + "=" * (-len(text) % 4))
        except (binascii.Error, ValueError):
            raw = b""
    if len(raw) > 17 and raw[0] == 0xEE:
        raise ValueError("fake-TLS MTProto secrets (starting with ee) are not supported; "
                         "use a plain or dd secret of the proxy")
    if len(raw) == 17 and raw[0] == 0xDD:
        return raw[1:].hex(), True
    if len(raw) == 16:
        return raw.hex(), False
    raise ValueError("an MTProto proxy secret is 32 hex characters, or dd and 32 hex characters")


def _proxy(name=None) -> dict:
    """The TelegramClient options for a proxy name: `proxy` (and `connection` for an MTProto proxy), or no
    options for a direct connection."""
    from feedxcavator import fetch

    if not name:
        return {}
    proxy = fetch.resolve_proxy(name)
    if not proxy:  # fail closed, as fetch_url does: a misspelt name must not mean a direct connection
        raise LookupError(f"proxy '{name}' is not defined")
    if fetch.is_mtproto(proxy):
        from telethon import connection

        try:
            key, dd = _mtproto_secret(proxy.get("secret"))
        except ValueError as e:
            raise ValueError(f"proxy '{name}': {e}") from None
        return {"proxy": (str(proxy["host"]), int(proxy["port"]), key),
                "connection": (connection.ConnectionTcpMTProxyRandomizedIntermediate if dd
                               else connection.ConnectionTcpMTProxyIntermediate)}
    schemes = {"socks5": "socks5", "socks5h": "socks5", "socks4": "socks4", "socks4a": "socks4"}
    return {"proxy": {
        "proxy_type": schemes.get(str(proxy.get("scheme")).lower(), "http"),
        "addr": proxy["host"], "port": int(proxy["port"]), "rdns": True,
        "username": proxy.get("user") or None, "password": proxy.get("password") or None,
    }}


def make_client(proxy=None):
    from telethon import TelegramClient

    session_path().parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(session_path()), int(conf.telegram("api-id")), str(conf.telegram("api-hash")),
                          **_proxy(proxy), device_model="PC", app_version="4",
                          # Telegram sends no login code to a client whose system_lang_code is a bare "en"
                          system_lang_code="en-US")


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


# login ----------------------------------------------------------------------------------------

_CODE_DELIVERY = {
    "SentCodeTypeApp": "to your Telegram app, in a chat from Telegram",
    "SentCodeTypeSms": "by SMS",
    "SentCodeTypeFirebaseSms": "by SMS",
    "SentCodeTypeFragmentSms": "by SMS through Fragment",
    "SentCodeTypeCall": "by a phone call",
    "SentCodeTypeFlashCall": "as a flash call: the code is the end of the calling number",
    "SentCodeTypeMissedCall": "as a missed call: the code is the end of the calling number",
    "SentCodeTypeEmailCode": "by email",
}


def _user_name(user) -> str:
    name = getattr(user, "username", None)
    return f"@{name}" if name else " ".join(filter(None, [getattr(user, "first_name", None),
                                                          getattr(user, "last_name", None)])) or str(user.id)


def login(qr: bool = False):
    """TG-6: the interactive login that creates the session: by QR code scanned in the Telegram app, or by
    phone number and the code Telegram sends (to the app, by SMS or call, or by email)."""
    if not configured():
        raise SystemExit("Set telegram.api-id and telegram.api-hash in config.yml first.")
    try:
        asyncio.run(_login(qr))
    except KeyboardInterrupt:
        raise SystemExit("\nLogin cancelled.") from None


async def _login(qr: bool):
    from telethon import errors

    client = make_client(proxy_name())
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            raise SystemExit(
                f"Already logged in as {_user_name(me)}{' (a bot)' if getattr(me, 'bot', False) else ''}. "
                f"To log in again, stop the server, delete {session_path()}.session and run telegram-login.")
        try:
            user = await (_qr_login(client) if qr else _code_login(client))
        except errors.SessionPasswordNeededError:
            user = await _password_login(client)
        print(f"Logged in as {_user_name(user)}. Session stored in {session_path()}.session")
    except errors.RPCError as e:
        raise SystemExit(f"Telegram refused the login: {type(e).__name__}: {e}") from None
    finally:
        await client.disconnect()


def _print_qr(code):
    """Half-block characters where the terminal can show them, else segno's colored spaces (twice as tall)."""
    import sys

    try:
        "▀▄█".encode(sys.stdout.encoding or "ascii")
        compact = True
    except (UnicodeEncodeError, LookupError):
        compact = False
    code.terminal(compact=compact)


async def _qr_login(client):
    import segno

    qr = await client.qr_login()
    while True:
        print("\nIn Telegram on your phone, open Settings > Devices > Link Desktop Device and scan this code:\n")
        _print_qr(segno.make(qr.url, error="l"))
        print(f"\nThe code is valid until {qr.expires.astimezone():%H:%M:%S}; a new one follows. Ctrl+C cancels.")
        try:
            return await qr.wait()
        except asyncio.TimeoutError:
            await qr.recreate()


async def _code_login(client):
    from telethon import errors, functions, types

    phone = input("Phone number, in international format (+71234567890): ").strip()
    sent = await client.send_code_request(phone)
    while True:
        if isinstance(sent.type, types.auth.SentCodeTypeSetUpEmailRequired):
            raise SystemExit("Telegram asks this account to set up a login email first, which only its own apps "
                             "can do. Log in with --qr instead.")
        by_email = isinstance(sent.type, types.auth.SentCodeTypeEmailCode)
        where = _CODE_DELIVERY.get(type(sent.type).__name__, f"({type(sent.type).__name__})")
        if by_email:
            where += f" to {sent.type.email_pattern}"
        print(f"Telegram sent the code {where}.")
        if sent.next_type is not None:
            print("Enter nothing to have it sent another way.")
        code = input("Code: ").strip().replace(" ", "")
        if not code:
            if sent.next_type is None:
                continue
            sent = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
            continue
        try:
            if by_email:  # Telethon's sign_in sends every code as a phone code, which Telegram rejects here
                result = await client(functions.auth.SignInRequest(
                    phone, sent.phone_code_hash, email_verification=types.EmailVerificationCode(code)))
                if isinstance(result, types.auth.AuthorizationSignUpRequired):
                    raise SystemExit("There is no Telegram account for this phone number.")
                return await client._on_login(result.user)
            return await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except errors.PhoneCodeInvalidError:
            print("Wrong code, try again.")
        except errors.BadRequestError as e:
            if e.message not in ("CODE_INVALID", "EMAIL_CODE_INVALID"):  # Telethon has no class for these
                raise
            print("Wrong code, try again.")
        except errors.PhoneCodeExpiredError:
            raise SystemExit("The code has expired. Telegram also expires a code that was sent in any Telegram "
                             "chat; type it in rather than forwarding or pasting it there.") from None


async def _password_login(client):
    import getpass

    from telethon import errors

    while True:
        password = getpass.getpass("Two-step verification is on. Cloud password: ")
        try:
            return await client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            print("Wrong password, try again.")
