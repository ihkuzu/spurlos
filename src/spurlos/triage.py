"""Put everything together: who has your data, how much, and what to do about it."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from . import census as census_mod
from .classify import DATA_TRADE, SENSITIVE, classify
from .llm import LLM, LLMError
from .registry import Registry
from .signals import STRONG, collect
from .vault import Vault

PRIORITY = {"high": 0, "medium": 1, "low": 2}
# Firms you usually have a contract with, so they hold far more than an address.
CONTRACTS = {"finance", "insurance", "telecommunication", "utility"}


@dataclass
class Item:
    domain: str
    name: str
    messages: int
    last_seen: str | None
    dormant: bool
    category: str
    category_source: str
    signals: dict[str, int] = field(default_factory=dict)
    action: str = "review"
    priority: str = "low"
    reason: str = ""
    record: str | None = None
    contact: str | None = None
    contact_kind: str | None = None


def decide(item: Item, newsletter_share: float) -> None:
    strong = STRONG & set(item.signals)
    if item.category in DATA_TRADE:
        item.action, item.priority = "access+objection", "high"
        item.reason = "trades in data about people; ask what they have and object to its use"
    elif item.category in SENSITIVE:
        item.action, item.priority = "access", "high"
        item.reason = "may hold special category data (Art. 9); see what they keep first"
    elif item.dormant and strong:
        item.action, item.priority = "erasure", "high"
        item.reason = (
            "forgotten account: " + ", ".join(sorted(strong)) + " mails, none for 2+ years"
        )
    elif item.dormant:
        item.action, item.priority = "erasure", "medium"
        item.reason = "no mail for 2+ years"
    elif strong or item.category in CONTRACTS:
        strong = strong or {item.category}
        item.action, item.priority = "access", "medium"
        item.reason = "active account (" + ", ".join(sorted(strong)) + "); deleting would close it"
    elif newsletter_share >= 0.8:
        item.action, item.priority = "unsubscribe", "low"
        item.reason = "newsletter only; the unsubscribe link is enough"
    else:
        item.reason = "few signals; decide yourself"


def build(
    vault: Vault,
    registry: Registry,
    llm: LLM | None = None,
    refresh: bool = False,
    on_error=None,
) -> list[Item]:
    result = census_mod.build(vault)
    now_dormant = {o.domain for o in result.dormant()}
    items = []
    for org in result.orgs:
        subjects = vault.subjects(org.domain)
        record = registry.lookup(org.domain)
        cached = None if refresh else vault.category(org.domain)
        if cached and not (cached[1] == "none" and llm is not None):
            category, source = cached
        else:
            try:
                category, source = classify(org.name, org.domain, subjects, record, llm)
            except LLMError as error:
                if on_error:
                    on_error(org.domain, error)
                category, source = classify(org.name, org.domain, subjects, record, None)
            vault.set_category(org.domain, category, source)
        item = Item(
            domain=org.domain,
            name=record.name if record else org.name,
            messages=org.messages,
            last_seen=org.last_seen,
            dormant=org.domain in now_dormant,
            category=category,
            category_source=source,
            signals=collect(subjects),
            record=record.slug if record else None,
        )
        own = vault.contact(org.domain)
        if own:
            item.contact = own["email"] or own["webform"]
            item.contact_kind = "email" if own["email"] else "webform"
            item.name = own["name"] or item.name
            item.record = "own"
        elif record:
            if record.transport == "webform" and record.webform:
                item.contact, item.contact_kind = record.webform, "webform"
            elif record.email:
                item.contact, item.contact_kind = record.email, "email"
            else:
                item.contact, item.contact_kind = record.address.replace("\n", ", "), "letter"
        decide(item, org.newsletter_share)
        items.append(item)
    items.sort(key=lambda i: (PRIORITY[i.priority], -i.messages))
    return items


def to_json(items: list[Item]) -> str:
    return json.dumps([asdict(i) for i in items], indent=2, ensure_ascii=False)


def format_report(items: list[Item], limit: int = 25) -> str:
    known = sum(1 for i in items if i.contact)
    counts = {p: sum(1 for i in items if i.priority == p) for p in PRIORITY}
    lines = [
        f"{len(items)} organisations, {known} with a known privacy contact.",
        f"priority: {counts['high']} high, {counts['medium']} medium, {counts['low']} low",
        "",
    ]
    for item in items[:limit]:
        contact = item.contact or "no contact in the registry yet"
        lines.append(f"[{item.priority:6}] {item.name} ({item.domain}) - {item.category}")
        lines.append(f"         {item.action}: {item.reason}")
        lines.append(f"         contact: {contact}")
    if len(items) > limit:
        lines.append(f"... and {len(items) - limit} more (use --limit 0 to list all)")
    return "\n".join(lines)
