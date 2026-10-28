"""SMTP sending from the rotated inbox pool.

We own deliverability here (D9/D15), so bounce classification matters: an SMTP
5xx means the contact is dead, a 4xx means try again later, and a socket error
means the inbox itself may be unhealthy.
"""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from app import constants as c
from app.channels.base import SendResult
from app.config import Settings, get_settings

_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"(?i)<br\s*/?>|</p>")
_BLOCK_RE = re.compile(r"(?i)</(div|tr|h[1-6]|li)>")


def resolve_secret(ref: str) -> str:
    """Resolve a secret reference to its value.

    ``smtp_password_ref`` names an environment variable (or a secret-store key in
    a later iteration). Never store the password itself in the database.
    """
    if not ref:
        return ""
    return os.environ.get(ref, "")


def html_to_text(html: str) -> str:
    """Cheap HTML -> text for the multipart/alternative plain part."""
    text = _BR_RE.sub("\n", html or "")
    text = _BLOCK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class SMTPEmailChannel:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(
        self,
        *,
        inbox: Any,
        to_address: str,
        from_name: str,
        from_email: str,
        reply_to: str,
        subject: str,
        body: str,
        headers: dict[str, str] | None = None,
    ) -> SendResult:
        host = getattr(inbox, "smtp_host", "") or ""
        if not host:
            return SendResult(
                ok=False,
                error=f"Inbox {getattr(inbox, 'email', '?')} has no SMTP host configured",
                retryable=False,
            )

        port = int(getattr(inbox, "smtp_port", 587) or 587)
        username = getattr(inbox, "smtp_username", "") or ""
        password = resolve_secret(getattr(inbox, "smtp_password_ref", "") or "")
        use_tls = bool(getattr(inbox, "use_tls", True))

        message = EmailMessage()
        message["From"] = formataddr((from_name, from_email))
        message["To"] = to_address
        message["Subject"] = subject
        if reply_to:
            message["Reply-To"] = reply_to
        domain = from_email.split("@")[-1] if "@" in from_email else "localhost"
        message_id = make_msgid(domain=domain)
        message["Message-ID"] = message_id
        for key, value in (headers or {}).items():
            message[key] = value

        message.set_content(html_to_text(body) or body)
        if body.strip().startswith("<"):
            message.add_alternative(body, subtype="html")

        try:
            with smtplib.SMTP(host, port, timeout=self.settings.smtp_timeout_seconds) as smtp:
                smtp.ehlo()
                if use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)
        except smtplib.SMTPRecipientsRefused as exc:
            code = next(iter(exc.recipients.values()))[0]
            bounce = c.BOUNCE_HARD if 500 <= code < 600 else c.BOUNCE_SOFT
            return SendResult(
                ok=False,
                error=f"Recipient refused ({code})",
                bounce_type=bounce,
                raw={"code": code},
            )
        except smtplib.SMTPResponseException as exc:
            retryable = 400 <= int(exc.smtp_code or 0) < 500
            return SendResult(
                ok=False,
                error=f"SMTP {exc.smtp_code}: {exc.smtp_error!r}",
                retryable=retryable,
                raw={"code": exc.smtp_code},
            )
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Connection-level problems are the inbox's problem, not the contact's.
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}", retryable=True)

        return SendResult(ok=True, provider_message_id=message_id)
