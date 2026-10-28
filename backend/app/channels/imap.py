"""IMAP reply detection.

Polls each inbox for unseen messages and parses them into ``InboundEmail``
objects. Thread matching is done by the ingest layer using the ``Message-ID`` /
``In-Reply-To`` / ``References`` hints collected here.

Only stdlib ``imaplib`` + ``email`` are used, so there is nothing extra to
install and nothing to keep patched for security updates.
"""

from __future__ import annotations

import email
import imaplib
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

from app.channels.base import InboundEmail
from app.channels.smtp import resolve_secret
from app.config import Settings, get_settings


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError):
        return value


def _extract_body(message: Message) -> str:
    """Prefer text/plain; fall back to HTML with the tags stripped."""
    plain: str | None = None
    html: str | None = None
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition.lower():
                continue
            if content_type not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
            except (LookupError, ValueError):
                continue
            if content_type == "text/plain" and plain is None:
                plain = decoded
            elif content_type == "text/html" and html is None:
                html = decoded
    else:
        try:
            payload = message.get_payload(decode=True) or b""
            charset = message.get_content_charset() or "utf-8"
            plain = payload.decode(charset, errors="replace")
        except (LookupError, ValueError):
            plain = str(message.get_payload())

    if plain:
        return plain.strip()
    if html:
        from app.channels.smtp import html_to_text

        return html_to_text(html)
    return ""


def parse_message(raw: bytes) -> InboundEmail:
    message = email.message_from_bytes(raw)
    received_at = None
    try:
        parsed = parsedate_to_datetime(message.get("Date", ""))
        received_at = parsed.astimezone(timezone.utc) if parsed else None
    except (TypeError, ValueError):
        received_at = datetime.now(timezone.utc)

    references = [ref for ref in _split_ids(message.get("References", ""))]
    headers = {key.lower(): str(value) for key, value in message.items()}

    return InboundEmail(
        from_address=_decode(message.get("From", "")),
        to_address=_decode(message.get("To", "")),
        subject=_decode(message.get("Subject", "")),
        body=_extract_body(message),
        message_id=(message.get("Message-ID") or "").strip(),
        in_reply_to=(message.get("In-Reply-To") or "").strip(),
        references=references,
        headers=headers,
        received_at=received_at,
        raw={"message_id": message.get("Message-ID", "")},
    )


def _split_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [token.strip() for token in str(value).replace(",", " ").split() if token.strip()]


class IMAPEmailSource:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def poll(self, inbox: Any) -> list[InboundEmail]:
        host = getattr(inbox, "imap_host", "") or ""
        if not host:
            return []
        port = int(getattr(inbox, "imap_port", 993) or 993)
        username = getattr(inbox, "smtp_username", "") or getattr(inbox, "email", "")
        password = resolve_secret(getattr(inbox, "smtp_password_ref", "") or "")

        messages: list[InboundEmail] = []
        try:
            if getattr(inbox, "use_tls", True):
                connection: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port)
            else:
                connection = imaplib.IMAP4(host, port)
        except (imaplib.IMAP4.error, OSError):
            return []

        with connection as conn:
            try:
                conn.login(username, password)
                conn.select("INBOX")
                status, data = conn.search(None, "UNSEEN")
                if status != "OK" or not data or not data[0]:
                    return []
                for number in data[0].split():
                    status, payload = conn.fetch(number, "(RFC822)")
                    if status != "OK" or not payload:
                        continue
                    for chunk in payload:
                        if isinstance(chunk, tuple) and len(chunk) == 2:
                            messages.append(parse_message(chunk[1]))
                    conn.store(number, "+FLAGS", "\\Seen")
            except (imaplib.IMAP4.error, OSError):
                return messages
        return messages
