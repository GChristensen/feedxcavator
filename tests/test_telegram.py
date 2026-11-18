"""The interactive login, with a fake Telethon client."""

import datetime as dt

import pytest
from telethon import errors, functions, types

from feedxcavator import conf, telegram


class FakeQR:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.recreated = 0
        self.url = "tg://login?token=AQAB"
        self.expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)

    async def wait(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def recreate(self):
        self.recreated += 1


class FakeClient:
    def __init__(self, authorized=False, sent=None, sign_in=None, requests=None, qr=None):
        self.authorized = authorized
        self.sent = sent
        self.sign_in_results = sign_in or []
        self.request_results = requests or []
        self.qr = qr
        self.sign_ins, self.requests, self.logged_in = [], [], []
        self.disconnected = False

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return types.User(id=7, bot=True, username="somebot")

    async def send_code_request(self, phone):
        return self.sent

    async def sign_in(self, *args, **kwargs):
        self.sign_ins.append((args, kwargs))
        result = self.sign_in_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def qr_login(self):
        return self.qr

    async def __call__(self, request):
        self.requests.append(request)
        result = self.request_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def _on_login(self, user):
        self.logged_in.append(user)
        return user


USER = types.User(id=1, username="me")


def sent(code_type, next_type=None):
    return types.auth.SentCode(type=code_type, phone_code_hash="hash", next_type=next_type)


@pytest.fixture
def login(monkeypatch):
    """Runs telegram.login with the given client and typed lines; returns the printed output."""
    monkeypatch.setitem(conf.config, "telegram", {"api-id": 1, "api-hash": "abc"})

    def run(client, lines=(), qr=False):
        lines = list(lines)
        printed = []
        monkeypatch.setattr(telegram, "make_client", lambda proxy=None: client)
        monkeypatch.setattr("builtins.input", lambda prompt="": lines.pop(0))
        monkeypatch.setattr("getpass.getpass", lambda prompt="": lines.pop(0))
        monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
        monkeypatch.setattr(telegram, "_print_qr", lambda code: printed.append("[QR]"))
        try:
            telegram.login(qr=qr)
        finally:
            assert client.disconnected
        return "\n".join(printed)

    return run


def test_login_refuses_an_existing_session(login):
    with pytest.raises(SystemExit, match=r"Already logged in as @somebot \(a bot\).*delete"):
        login(FakeClient(authorized=True))


def test_login_by_app_code_with_retry_and_password(login):
    client = FakeClient(sent=sent(types.auth.SentCodeTypeApp(length=5)),
                        sign_in=[errors.PhoneCodeInvalidError(None), errors.SessionPasswordNeededError(None),
                                 errors.PasswordHashInvalidError(None), USER])
    out = login(client, ["+71234567890", "11111", "12 345", "wrong", "right"])
    assert "to your Telegram app" in out and "Wrong code" in out and "Wrong password" in out
    assert "Logged in as @me" in out
    assert client.sign_ins[1] == (("+71234567890", "12345"), {"phone_code_hash": "hash"})
    assert client.sign_ins[-1] == ((), {"password": "right"})


def test_login_resends_and_takes_an_email_code(login):
    first = sent(types.auth.SentCodeTypeApp(length=5), next_type=types.auth.CodeTypeSms())
    by_email = sent(types.auth.SentCodeTypeEmailCode(email_pattern="m***@example.com", length=6))
    client = FakeClient(sent=first, requests=[by_email, types.auth.Authorization(user=USER)])
    out = login(client, ["+71234567890", "", "654321"])
    assert "another way" in out and "by email to m***@example.com" in out
    resend, sign_in = client.requests
    assert isinstance(resend, functions.auth.ResendCodeRequest)
    assert isinstance(sign_in, functions.auth.SignInRequest) and sign_in.phone_code is None
    assert sign_in.email_verification == types.EmailVerificationCode(code="654321")
    assert client.logged_in == [USER] and not client.sign_ins


def test_login_explains_an_expired_code_and_email_setup(login):
    client = FakeClient(sent=sent(types.auth.SentCodeTypeSms(length=5)),
                        sign_in=[errors.PhoneCodeExpiredError(None)])
    with pytest.raises(SystemExit, match="expired"):
        login(client, ["+71234567890", "12345"])
    client = FakeClient(sent=sent(types.auth.SentCodeTypeSetUpEmailRequired()))
    with pytest.raises(SystemExit, match="--qr"):
        login(client, ["+71234567890"])


def test_login_by_qr_renews_the_code_and_asks_the_password(login):
    import asyncio

    qr = FakeQR([asyncio.TimeoutError(), errors.SessionPasswordNeededError(None)])
    client = FakeClient(qr=qr, sign_in=[USER])
    out = login(client, ["secret"], qr=True)
    assert out.count("[QR]") == 2 and qr.recreated == 1 and "Link Desktop Device" in out
    assert client.sign_ins == [((), {"password": "secret"})] and "Logged in as @me" in out


def test_login_reports_other_telegram_errors(login):
    client = FakeClient(sent=None)

    async def flood(phone):
        raise errors.FloodWaitError(None, capture=60)

    client.send_code_request = flood
    with pytest.raises(SystemExit, match="Telegram refused the login: FloodWaitError"):
        login(client, ["+71234567890"])
