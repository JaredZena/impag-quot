"""services/mailer.py: one transport for every email, never raises."""

from typing import ClassVar

import pytest

from services import mailer

MESSAGE = {
    "from": "Todo Para El Campo <cotizaciones@todoparaelcampo.com.mx>",
    "to": ["juan@example.com"],
    "reply_to": "impagtodoparaelcampo@gmail.com",
    "subject": "Tu cotización",
    "html": "<p>Hola</p>",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("RESEND_API_KEY", "GMAIL_SMTP_USER", "GMAIL_SMTP_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


class FakeSMTP:
    sent: ClassVar[list] = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.login_args = (user, password)
        FakeSMTP.sent.append(("login", user, password))

    def send_message(self, email):
        FakeSMTP.sent.append(("send", email))


def test_nothing_configured_sends_nothing():
    assert mailer.transport() is None
    assert mailer.send(MESSAGE, tag="t") is False


def test_gmail_sends_from_the_account_under_the_message_display_name(monkeypatch):
    monkeypatch.setenv("GMAIL_SMTP_USER", "impagtodoparaelcampo@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "abcd efgh ijkl mnop")
    FakeSMTP.sent = []
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    assert mailer.transport() == "gmail"
    assert mailer.send(MESSAGE, tag="t") is True
    (_, user, password), (_, email) = FakeSMTP.sent
    assert user == "impagtodoparaelcampo@gmail.com"
    assert password == "abcdefghijklmnop"  # Google's spaces are not part of it
    assert email["From"] == "Todo Para El Campo <impagtodoparaelcampo@gmail.com>"
    assert email["To"] == "juan@example.com"
    assert email["Reply-To"] == "impagtodoparaelcampo@gmail.com"
    assert "<p>Hola</p>" in email.as_string()


def test_resend_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("GMAIL_SMTP_USER", "impagtodoparaelcampo@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "x")
    calls = []

    class Response:
        status_code = 200

    def post(url, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(mailer.requests, "post", post)
    assert mailer.send(MESSAGE, tag="t", idempotency_key="k1") is True
    [kwargs] = calls
    assert kwargs["headers"]["Idempotency-Key"] == "k1"
    assert kwargs["json"]["from"] == MESSAGE["from"]


def test_failures_never_raise(monkeypatch):
    monkeypatch.setenv("GMAIL_SMTP_USER", "impagtodoparaelcampo@gmail.com")
    monkeypatch.setenv("GMAIL_SMTP_APP_PASSWORD", "x")

    def boom(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", boom)
    assert mailer.send(MESSAGE, tag="t") is False
    assert mailer.send({**MESSAGE, "to": []}, tag="t") is False
