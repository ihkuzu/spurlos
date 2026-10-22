"""Turn a request into an email and send it from your own account."""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from .requests import is_reserved


class SendError(RuntimeError):
    pass


def build(request, sender: str, name: str, bcc_self: bool = True) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{name} <{sender}>"
    msg["To"] = request["recipient"]
    if bcc_self:
        # a copy in your own inbox is your proof of the date you sent it
        msg["Bcc"] = sender
    msg["Subject"] = request["subject"]
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["Message-ID"] = make_msgid(idstring=request["id"], domain=sender.rsplit("@", 1)[1])
    msg.set_content(request["body"])
    return msg


def export_eml(msg: EmailMessage, path: str | Path) -> Path:
    """An unsent message that Thunderbird or Outlook opens as a draft."""
    draft = EmailMessage()
    for key, value in msg.items():
        if key != "Bcc":
            draft[key] = value
    draft["X-Unsent"] = "1"
    draft.set_content(msg.get_content())
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(draft))
    return path


def send(msg: EmailMessage, host: str, port: int, user: str, password: str) -> None:
    for header in ("To", "Bcc"):
        address = msg.get(header)
        if address and is_reserved(address):
            raise SendError(f"{address} is a reserved example address; nothing was sent")
    context = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=60) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as error:
        raise SendError(f"sending failed: {error}") from error
