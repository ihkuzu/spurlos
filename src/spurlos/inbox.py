"""Find the answers to your requests in a mailbox and let the agent handle them."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from . import agent
from .llm import LLM
from .orgs import org_domain
from .replies import Reply, parse
from .vault import Vault


def watched(vault: Vault) -> tuple[set[str], date | None]:
    """Organisations with a sent request, and the earliest send date."""
    orgs, first = set(), None
    for r in vault.requests():
        if r["status"] == "draft":
            continue
        orgs.add(r["org"])
        orgs.add(org_domain(r["recipient"].rsplit("@", 1)[-1]))
        day = date.fromisoformat(r["sent"][:10]) if r["sent"] else None
        if day and (first is None or day < first):
            first = day
    return orgs, first


def relevant(reply: Reply, orgs: set[str], mine: set[str]) -> bool:
    if reply.sender in mine:
        return False
    return bool(reply.refs) or reply.org in orgs


def imap_criteria(orgs: set[str], since: date | None) -> list[list[str]]:
    """Server-side searches, so only candidate replies are ever downloaded."""
    base = ["SINCE", since.strftime("%d-%b-%Y")] if since else []
    searches = [[*base, "SUBJECT", "SP-"]]
    searches += [[*base, "FROM", org] for org in sorted(orgs)]
    return searches


def process(vault: Vault, raws: Iterable[bytes], llm: LLM | None, apply: bool = True) -> list:
    orgs, _ = watched(vault)
    mine = vault.me() | {vault.profile().get("email", "").lower()}
    requests = [dict(r) for r in vault.requests()]
    graph = agent.build(llm, requests)
    results = []
    for raw in raws:
        reply = parse(raw)
        if reply is None or not relevant(reply, orgs, mine) or vault.has_reply(reply.key):
            continue
        result = graph.invoke({"reply": reply})
        vault.add_reply(reply, result)
        decision = result.get("decision") or {}
        req = result.get("request")
        if apply and req and decision.get("status"):
            # an answer to a reminder or complaint settles the request it belongs to
            target = req.get("parent") or req["id"]
            vault.set_status(target, decision["status"], decision.get("due"))
            if target != req["id"]:
                vault.set_status(req["id"], "answered")
        results.append((reply, result))
    return results


def format_results(results: list) -> str:
    if not results:
        return "no new replies to your requests"
    lines = []
    for reply, result in results:
        req = result.get("request")
        if not req:
            lines.append(f"? {reply.sender}: {reply.subject[:60]} (could not tell which request)")
            continue
        decision = result.get("decision") or {}
        lines.append(f"{req['id']} {req['org']}: {result['label']} ({result['source']})")
        if result.get("summary"):
            lines.append(f"    {result['summary']}")
        lines.append(f"    next: {decision.get('next', '')}")
    return "\n".join(lines)
