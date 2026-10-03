"""
Outgoing email for every message the backend sends.

Two transports, read per call so an App Runner change applies without a deploy:
- Resend (RESEND_API_KEY): the sender's domain must be verified in Resend
  (DKIM/SPF records at the domain's DNS, HostGator for todoparaelcampo.com.mx).
- Gmail SMTP (GMAIL_SMTP_USER + GMAIL_SMTP_APP_PASSWORD, an app password of
  the store's Gmail account): no DNS work. The message goes out from that
  account under the display name of the message's "from", and replies land in
  its inbox.

Resend wins when both are set. send() never raises: callers run it in a
background task after the database commit. Logs never carry an address or a
secret.
"""

import base64
import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import requests

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465
SEND_TIMEOUT_SECONDS = 10


def _env(name: str) -> str | None:
    return (os.getenv(name) or "").strip() or None


def _gmail_credentials() -> tuple[str, str] | None:
    user = _env("GMAIL_SMTP_USER")
    # Google shows app passwords in groups of four; the spaces are not part of it.
    password = (_env("GMAIL_SMTP_APP_PASSWORD") or "").replace(" ", "")
    return (user, password) if user and password else None


def transport() -> str | None:
    """'resend', 'gmail', or None when no email can be sent."""
    if _env("RESEND_API_KEY"):
        return "resend"
    if _gmail_credentials():
        return "gmail"
    return None


def configured() -> bool:
    return transport() is not None


def send(message: dict, *, tag: str, idempotency_key: str | None = None) -> bool:
    """Send {from, to: [..], subject, html, text?, reply_to?, attachments?}
    (attachments: [{filename, content: bytes, content_type}]). `tag` names the
    message in the logs (e.g. "web quote WEB-261002-KM2CG2 staff alert")."""
    to = [addr for addr in message.get("to") or [] if addr]
    if not to:
        return False
    kind = transport()
    if kind is None:
        logger.warning("%s: not sent, no email transport configured", tag)
        return False
    try:
        if kind == "resend":
            ok = _send_resend(message, to, idempotency_key)
        else:
            ok = _send_gmail(message, to)
    except Exception as exc:  # noqa: BLE001 - a background task must never raise
        logger.error("%s: send failed (%s): %s", tag, kind, type(exc).__name__)
        return False
    if ok:
        logger.info("%s: sent via %s", tag, kind)
    else:
        logger.error("%s: rejected by %s", tag, kind)
    return ok


def _send_resend(message: dict, to: list[str], idempotency_key: str | None) -> bool:
    headers = {"Authorization": f"Bearer {_env('RESEND_API_KEY')}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    payload = {
        "from": message["from"],
        "to": to,
        "subject": message["subject"],
        "html": message["html"],
    }
    if message.get("text"):
        payload["text"] = message["text"]
    if message.get("reply_to"):
        payload["reply_to"] = message["reply_to"]
    if message.get("attachments"):
        payload["attachments"] = [
            {
                "filename": a["filename"],
                "content": base64.b64encode(a["content"]).decode("ascii"),
            }
            for a in message["attachments"]
        ]
    response = requests.post(
        RESEND_URL, headers=headers, json=payload, timeout=SEND_TIMEOUT_SECONDS
    )
    return response.status_code < 300


def _send_gmail(message: dict, to: list[str]) -> bool:
    user, password = _gmail_credentials()
    display_name, _ = parseaddr(message.get("from") or "")
    email = EmailMessage()
    email["From"] = formataddr((display_name or "Todo Para El Campo", user))
    email["To"] = ", ".join(to)
    email["Subject"] = message["subject"]
    if message.get("reply_to"):
        email["Reply-To"] = message["reply_to"]
    email.set_content(message.get("text") or "Abre este correo en formato HTML.")
    email.add_alternative(message["html"], subtype="html")
    for attachment in message.get("attachments") or []:
        maintype, _, subtype = attachment["content_type"].partition("/")
        email.add_attachment(
            attachment["content"],
            maintype=maintype,
            subtype=subtype,
            filename=attachment["filename"],
        )
    with smtplib.SMTP_SSL(
        GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=SEND_TIMEOUT_SECONDS
    ) as smtp:
        smtp.login(user, password)
        smtp.send_message(email)
    return True
