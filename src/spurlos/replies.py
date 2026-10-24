"""Answers from companies. Only replies to your own requests are read in full."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from .orgs import org_domain

REF = re.compile(r"\bSP-[A-HJ-NP-Z2-9]{6}\b")
# Where a quoted earlier mail starts in a reply.
_QUOTE_START = re.compile(
    r"^(-{2,} ?(original|ursprüngliche) (message|nachricht) ?-{2,}|"
    r"(on|am) .{4,80} (wrote|schrieb)[^\n]*:\s*$|von: .+|from: .+@.+)",
    re.IGNORECASE | re.MULTILINE,
)
MAX_BODY = 6000


@dataclass
class Reply:
    key: str
    sender: str
    org: str
    date: datetime | None
    subject: str
    body: str
    in_reply_to: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    auto: bool = False

    @property
    def refs(self) -> set[str]:
        return set(REF.findall(self.subject)) | set(REF.findall(self.body))


def _text_of(msg) -> tuple[str, list[str]]:
    plain, rich, files = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if name or part.get_content_disposition() == "attachment":
            files.append(name or "attachment")
            continue
        kind = part.get_content_type()
        try:
            content = part.get_content()
        except (LookupError, ValueError):
            continue
        if kind == "text/plain":
            plain.append(content)
        elif kind == "text/html":
            rich.append(content)
    if plain:
        return "\n".join(plain), files
    text = "\n".join(rich)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t]+", " ", text), files


def strip_quotes(body: str) -> str:
    """Keep what the company wrote, not the request it quotes back."""
    match = _QUOTE_START.search(body)
    if match and match.start() > 0:
        body = body[: match.start()]
    lines = [line for line in body.splitlines() if not line.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse(raw: bytes) -> Reply | None:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    pairs = getaddresses([str(msg.get("From", ""))])
    if not pairs or "@" not in pairs[0][1]:
        return None
    sender = pairs[0][1].lower()
    try:
        date = parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
        if date and date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        date = None
    body, files = _text_of(msg)
    ids = " ".join(str(msg.get(h, "")) for h in ("In-Reply-To", "References"))
    auto = str(msg.get("Auto-Submitted", "no")).lower() not in ("", "no") or bool(
        msg.get("X-Autoreply") or msg.get("X-Autorespond")
    )
    key_src = str(msg.get("Message-ID") or f"{sender}|{date}|{msg.get('Subject')}")
    return Reply(
        key=hashlib.sha256(key_src.encode("utf-8", "replace")).hexdigest()[:32],
        sender=sender,
        org=org_domain(sender.rsplit("@", 1)[1]),
        date=date.astimezone(timezone.utc) if date else None,
        subject=str(msg.get("Subject", "")),
        body=strip_quotes(body)[:MAX_BODY],
        in_reply_to=re.findall(r"<[^>]+>", ids),
        attachments=files,
        auto=auto,
    )


def match(reply: Reply, requests: list) -> tuple[object | None, str]:
    """Find the request a reply belongs to: thread, then reference, then sender domain."""
    sent = [r for r in requests if r["status"] != "draft"]
    for r in sent:
        if r["message_id"] and r["message_id"] in reply.in_reply_to:
            return r, "thread"
    refs = reply.refs
    for r in sent:
        if r["id"] in refs:
            return r, "reference"
    same_org = []
    for r in sent:
        recipient_org = org_domain(r["recipient"].rsplit("@", 1)[-1])
        if reply.org in (r["org"], recipient_org):
            same_org.append(r)
    if reply.date:
        same_org = [
            r for r in same_org if not r["sent"] or r["sent"][:10] <= reply.date.isoformat()
        ]
    if len(same_org) == 1:
        return same_org[0], "sender"
    return None, "ambiguous" if same_org else "none"
