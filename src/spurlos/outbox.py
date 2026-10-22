"""Create request drafts for organisations and keep track of what was sent when."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import requests as req
from .orgs import org_domain
from .registry import Record, Registry
from .triage import Item
from .vault import Vault

# What a triage action turns into.
ACTION_KINDS = {
    "erasure": ["erasure"],
    "access": ["access"],
    "access+objection": ["access", "objection"],
}


class NoContact(LookupError):
    pass


@dataclass
class Contact:
    recipient: str
    transport: str
    record: Record | None


def contact_for(vault: Vault, registry: Registry, domain: str, slug: str | None = None) -> Contact:
    org = org_domain(domain)
    own = vault.contact(org)
    if own and not slug:
        if own["email"]:
            return Contact(own["email"], "email", None)
        return Contact(own["webform"], "webform", None)
    record = registry.records.get(slug) if slug else registry.lookup(org)
    if record is None:
        raise NoContact(
            f"no privacy contact for {org}; add one with: spurlos contact {org} --email ..."
        )
    if record.transport == "webform" and record.webform:
        return Contact(record.webform, "webform", record)
    if record.email:
        return Contact(record.email, "email", record)
    return Contact(record.address.replace("\n", ", "), "letter", record)


def create(
    vault: Vault,
    registry: Registry,
    domain: str,
    kind: str,
    slug: str | None = None,
    lang: str | None = None,
    fields: dict[str, str] | None = None,
    portability: bool = False,
    objection: bool = False,
) -> req.Draft:
    org = org_domain(domain)
    existing = vault.open_request(org, kind)
    if existing:
        raise ValueError(f"there is already an open {kind} request for {org}: {existing['id']}")
    contact = contact_for(vault, registry, org, slug)
    d = req.draft(
        registry, org, kind, vault.profile(), contact.recipient, contact.transport,
        record=contact.record, lang=lang, fields=fields, portability=portability,
        objection=objection,
    )  # fmt: skip
    vault.add_request(d)
    return d


def from_triage(
    vault: Vault, registry: Registry, items: list[Item], priority: str = "high"
) -> tuple[list[req.Draft], list[str]]:
    """Drafts for every triage item at this priority that has an email contact."""
    made, skipped = [], []
    for item in items:
        if item.priority != priority or item.action not in ACTION_KINDS:
            continue
        if item.contact_kind != "email":
            skipped.append(f"{item.domain}: {item.contact_kind or 'no contact'}")
            continue
        for kind in ACTION_KINDS[item.action]:
            try:
                slug = None if item.record in (None, "own") else item.record
                made.append(create(vault, registry, item.domain, kind, slug=slug))
            except (ValueError, NoContact) as error:
                skipped.append(f"{item.domain}: {error}")
    return made, skipped


def mark_sent(vault: Vault, request_id: str, when: datetime | None = None, message_id=None):
    when = when or datetime.now(timezone.utc)
    due = req.due_date(when)
    vault.mark_sent(request_id, when.isoformat(), due.isoformat(), message_id)
    return due


def status_lines(vault: Vault, today: date | None = None) -> list[str]:
    today = today or datetime.now(timezone.utc).date()
    lines = []
    for r in vault.requests():
        state = r["status"]
        if state == "sent" and r["due"]:
            left = (date.fromisoformat(r["due"]) - today).days
            state = f"sent {r['sent'][:10]}, " + (
                f"OVERDUE by {-left} days" if left < 0 else f"answer due in {left} days"
            )
        lines.append(f"{r['id']}  {r['kind']:9} {r['org']:30} {r['transport']:7} {state}")
    return lines
