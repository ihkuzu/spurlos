"""GDPR requests: filled from fixed legal templates, never written by a model."""

from __future__ import annotations

import calendar
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .registry import Record, Registry

KINDS = ("access", "erasure", "objection")
ARTICLE = {"access": "15", "erasure": "17", "objection": "21"}
SUBJECTS = {
    "de": {
        "access": "Anfrage auf Auskunft nach Art. 15 DSGVO",
        "erasure": "Antrag auf Löschung nach Art. 17 DSGVO",
        "objection": "Widerspruch nach Art. 21 DSGVO",
    },
    "en": {
        "access": "Request for access under Art. 15 GDPR",
        "erasure": "Request for erasure under Art. 17 GDPR",
        "objection": "Objection under Art. 21 GDPR",
    },
}
LABELS = {
    "de": {"name": "Name", "email": "E-Mail-Adresse", "address": "Anschrift", "ref": "Referenz"},
    "en": {
        "name": "Name",
        "email": "Email address",
        "address": "Postal address",
        "ref": "Reference",
    },
}
# Domains reserved for examples and tests; mail to them must never leave the machine.
RESERVED = (".example", ".test", ".invalid", ".localhost", ".local")
_BLOCK = re.compile(r"\[([a-z_]+)>(.*?)\]", re.DOTALL)
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@dataclass
class Draft:
    id: str
    org: str
    record: str | None
    kind: str
    lang: str
    recipient: str
    transport: str
    subject: str
    body: str


def new_ref() -> str:
    return "SP-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))


def add_month(day: date) -> date:
    """Art. 12(3): one month, so 31 January becomes 28 or 29 February."""
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def render(template: str, flags: set[str], values: dict[str, str]) -> str:
    """Fill the Datenanfragen.de template syntax: [flag>text] blocks and {name} fields."""
    text = _BLOCK.sub(lambda m: m.group(2) if m.group(1) in flags else "", template)
    text = re.sub(r"</?italic>", "", text)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    text = re.sub(r"\{[a-z_]+\}", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def pick_language(record: Record | None, wanted: str | None) -> str:
    if wanted:
        return wanted
    if record and record.language in ("de", "en"):
        return record.language
    if record and "de" in record.countries:
        return "de"
    return "de" if record is None else "en"


def identity(profile: dict, lang: str, extras: dict[str, str], ref: str, address: bool) -> str:
    """Only what is needed to find you: name and email, plus what the company asks for."""
    label = LABELS[lang]
    lines = [f"{label['name']}: {profile['name']}", f"{label['email']}: {profile['email']}"]
    if address and profile.get("address"):
        lines.append(f"{label['address']}: {profile['address']}")
    lines += [f"{k}: {v}" for k, v in extras.items()]
    lines.append(f"{label['ref']}: {ref}")
    return "\n".join(lines) + "\n"


def draft(
    registry: Registry,
    org: str,
    kind: str,
    profile: dict,
    recipient: str,
    transport: str,
    record: Record | None = None,
    lang: str | None = None,
    fields: dict[str, str] | None = None,
    portability: bool = False,
    objection: bool = False,
) -> Draft:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not profile.get("name") or not profile.get("email"):
        raise ValueError("set your name and email first: spurlos profile --name ... --email ...")
    lang = pick_language(record, lang)
    template = registry.templates[lang][f"{kind}-default"]
    ref = new_ref()
    flags = {"has_fields", "erase_all"}
    if portability:
        flags.add("data_portability")
    if objection and kind == "erasure":
        flags.add("include_objection")
    wants_address = bool(record and any(r.get("type") == "address" for r in record.required))
    values = {"id_data": identity(profile, lang, dict(fields or {}), ref, wants_address)}
    if record and record.runs:
        flags.add("runs")
        values["runs_list"] = ", ".join(record.runs)
    body = render(template, flags, values) + "\n" + profile["name"]
    return Draft(
        id=ref,
        org=org,
        record=record.slug if record else None,
        kind=kind,
        lang=lang,
        recipient=recipient,
        transport=transport,
        subject=f"{SUBJECTS[lang][kind]} ({ref})",
        body=body,
    )


def is_reserved(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower().rstrip(".")
    return any(domain.endswith(end) or domain == end[1:] for end in RESERVED)


def due_date(sent: datetime) -> date:
    return add_month(sent.astimezone(timezone.utc).date())
