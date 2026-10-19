"""Turn raw mail headers into the few fields spurlos keeps. Bodies are never read."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.parser import BytesHeaderParser
from email.utils import getaddresses, parsedate_to_datetime

# Only these headers are fetched over IMAP and kept in the vault.
FIELDS = ("From", "Date", "Subject", "Message-ID", "List-Unsubscribe")


@dataclass(frozen=True)
class Header:
    key: str
    address: str
    name: str
    date: datetime | None
    subject: str
    unsubscribe: bool
    source: str

    @property
    def domain(self) -> str:
        return self.address.rpartition("@")[2]


def _text(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value)))).strip()
    except (LookupError, UnicodeError, ValueError):
        return str(value).strip()


def _date(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_header(raw: bytes, source: str = "") -> Header | None:
    """Return None when the message has no usable sender."""
    msg = BytesHeaderParser().parsebytes(raw)
    pairs = getaddresses([_text(msg.get("From"))])
    if not pairs:
        return None
    name, address = pairs[0]
    address = address.strip().lower()
    if "@" not in address or address.endswith("@"):
        return None
    date = _date(msg.get("Date"))
    subject = _text(msg.get("Subject"))
    message_id = _text(msg.get("Message-ID"))
    if not message_id:
        stamp = date.isoformat() if date else ""
        message_id = f"{address}|{stamp}|{subject}"
    key = hashlib.sha256(message_id.encode("utf-8", "replace")).hexdigest()[:32]
    return Header(
        key=key,
        address=address,
        name=name.strip().strip('"'),
        date=date,
        subject=subject,
        unsubscribe=bool(msg.get("List-Unsubscribe")),
        source=source,
    )
