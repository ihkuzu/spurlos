"""Group stored headers by organisation: who has written to you, how often, since when."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from .orgs import FREEMAIL, label
from .vault import Vault

DORMANT_AFTER = timedelta(days=730)


@dataclass
class Org:
    domain: str
    name: str = ""
    messages: int = 0
    addresses: list[str] = field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    newsletter_share: float = 0.0

    def dormant(self, now: datetime) -> bool:
        if not self.last_seen:
            return False
        return now - datetime.fromisoformat(self.last_seen) > DORMANT_AFTER


@dataclass
class Census:
    orgs: list[Org]
    messages: int
    personal: int
    generated: str

    def dormant(self) -> list[Org]:
        now = datetime.fromisoformat(self.generated)
        return [o for o in self.orgs if o.dormant(now)]

    def to_json(self) -> str:
        return json.dumps(
            {
                "generated": self.generated,
                "messages": self.messages,
                "personal": self.personal,
                "orgs": [asdict(o) for o in self.orgs],
            },
            indent=2,
            ensure_ascii=False,
        )


def build(vault: Vault, now: datetime | None = None) -> Census:
    now = now or datetime.now(timezone.utc)
    me = vault.me()
    names: dict[str, Counter] = defaultdict(Counter)
    addresses: dict[str, set] = defaultdict(set)
    dates: dict[str, list[str]] = defaultdict(list)
    counts: Counter = Counter()
    unsub: Counter = Counter()
    personal = total = 0
    for row in vault.messages():
        total += 1
        if row["address"] in me:
            continue
        org = row["org"]
        if org in FREEMAIL:
            personal += 1
            continue
        counts[org] += 1
        unsub[org] += row["unsubscribe"]
        names[org][row["name"]] += 1
        addresses[org].add(row["address"])
        if row["date"]:
            dates[org].append(row["date"])
    orgs = []
    for org, n in counts.most_common():
        seen = sorted(dates[org])
        orgs.append(
            Org(
                domain=org,
                name=label(names[org], org),
                messages=n,
                addresses=sorted(addresses[org]),
                first_seen=seen[0] if seen else None,
                last_seen=seen[-1] if seen else None,
                newsletter_share=round(unsub[org] / n, 2),
            )
        )
    return Census(orgs=orgs, messages=total, personal=personal, generated=now.isoformat())


def _day(value: str | None) -> str:
    return value[:10] if value else "-"


def format_report(census: Census, limit: int = 25) -> str:
    lines = [
        (
            f"{len(census.orgs)} organisations have your email address "
            f"(from {census.messages} messages, {census.personal} from private mailboxes left out)."
        ),
        (
            f"{len(census.dormant())} of them have not written for over two years: "
            "likely forgotten accounts."
        ),
        "",
        f"{'organisation':28} {'domain':26} {'mails':>5}  {'first':10}  {'last':10}  newsletter",
    ]
    for org in census.orgs[:limit]:
        lines.append(
            f"{org.name[:28]:28} {org.domain[:26]:26} {org.messages:>5}  "
            f"{_day(org.first_seen):10}  {_day(org.last_seen):10}  {org.newsletter_share:.0%}"
        )
    if len(census.orgs) > limit:
        lines.append(f"... and {len(census.orgs) - limit} more (use --limit 0 to list all)")
    return "\n".join(lines)
