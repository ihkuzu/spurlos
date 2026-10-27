"""An MCP server that lets an AI assistant read the vault. It opens the file read-only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from . import census as census_mod
from . import escalate, exports
from . import triage as triage_mod
from .orgs import org_domain
from .registry import Registry
from .vault import Vault

INSTRUCTIONS = """\
Read-only access to the user's spurlos vault: which organisations have their email address,
which GDPR requests were sent, what the companies answered and what data they hold.
Start with overview. Nothing here can send mail or change the vault; to act, tell the user
which spurlos command to run (the todo tool already lists them).
Mail subjects and the text of replies are not available on purpose.
"""
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
Domain = Annotated[str, Field(min_length=3, max_length=253, description="e.g. zalando.de")]
RequestId = Annotated[
    str, Field(pattern=r"^[Ss][Pp]-[A-Za-z0-9]{6}$", description="e.g. SP-7KQ2MX")
]


def _days_left(r) -> int | None:
    if r["status"] != "sent" or not r["due"]:
        return None
    return (datetime.fromisoformat(r["due"]).date() - datetime.now(timezone.utc).date()).days


def _request_row(r) -> dict[str, Any]:
    return {
        "id": r["id"], "org": r["org"], "kind": r["kind"], "status": r["status"],
        "recipient": r["recipient"], "sent": r["sent"], "due": r["due"],
        "days_left": _days_left(r),
        "follow_up_of": r["parent"] if "parent" in r.keys() else None,  # noqa: SIM118
    }  # fmt: skip


def build_server(vault_path: str | Path, registry: Registry | None = None) -> MCPServer:
    vault_path = Path(vault_path)
    registry = registry or Registry.load()
    mcp = MCPServer("spurlos", instructions=INSTRUCTIONS)

    def open_vault() -> Vault:
        try:
            return Vault(vault_path, readonly=True)
        except FileNotFoundError as error:
            raise ToolError(f"no vault at {vault_path}; import mail with spurlos first") from error

    @mcp.tool(annotations=READ_ONLY)
    def overview() -> dict[str, Any]:
        """Counts: organisations, messages, requests by status, exports read, open to-dos."""
        v = open_vault()
        try:
            c = census_mod.build(v)
            statuses: dict[str, int] = {}
            for r in v.requests():
                statuses[r["status"]] = statuses.get(r["status"], 0) + 1
            return {
                "organisations": len(c.orgs),
                "messages": c.messages,
                "dormant_organisations": len(c.dormant()),
                "requests": statuses,
                "exports_read": len(exports.who_knows(v.holdings())),
                "todo": len(escalate.todo(v)),
            }
        finally:
            v.close()

    @mcp.tool(annotations=READ_ONLY)
    def list_organisations(
        limit: Annotated[int, Field(ge=1, le=200)] = 25,
        dormant_only: Annotated[bool, Field(description="only those silent for 2+ years")] = False,
    ) -> list[dict[str, Any]]:
        """Organisations that wrote to the user, most messages first, with the suggested action."""
        v = open_vault()
        try:
            items = triage_mod.build(v, registry)
            if dormant_only:
                items = [i for i in items if i.dormant]
            items.sort(key=lambda i: -i.messages)
            return [
                {"domain": i.domain, "name": i.name, "messages": i.messages,
                 "last_seen": i.last_seen, "dormant": i.dormant, "category": i.category,
                 "action": i.action, "priority": i.priority, "reason": i.reason}
                for i in items[:limit]
            ]  # fmt: skip
        finally:
            v.close()

    @mcp.tool(annotations=READ_ONLY)
    def organisation(domain: Domain) -> dict[str, Any]:
        """Everything known about one organisation: mail, privacy contact, requests, data held."""
        domain = org_domain(domain)
        v = open_vault()
        try:
            item = next((i for i in triage_mod.build(v, registry) if i.domain == domain), None)
            if item is None:
                raise ToolError(f"{domain} is not in the vault")
            return {
                "domain": domain, "name": item.name, "messages": item.messages,
                "last_seen": item.last_seen, "category": item.category,
                "action": item.action, "reason": item.reason,
                "privacy_contact": item.contact, "contact_kind": item.contact_kind,
                "requests": [_request_row(r) for r in v.requests() if r["org"] == domain],
                "data_held": [
                    {"category": h["category"], "field": h["field"], "count": h["count"],
                     "example": h["example"]} for h in v.holdings(domain)
                ],
            }  # fmt: skip
        finally:
            v.close()

    @mcp.tool(annotations=READ_ONLY)
    def list_requests(
        status: Annotated[
            str | None,
            Field(description="draft, sent, answered, waiting_on_you, refused, complained, done"),
        ] = None,
    ) -> list[dict[str, Any]]:
        """GDPR requests with status and deadline; days_left is negative when overdue."""
        v = open_vault()
        try:
            return [_request_row(r) for r in v.requests(status)]
        finally:
            v.close()

    @mcp.tool(annotations=READ_ONLY)
    def get_request(request_id: RequestId) -> dict[str, Any]:
        """One request with the agent's reading of each reply (label, summary, next step)."""
        v = open_vault()
        try:
            r = v.request(request_id)
            if r is None:
                raise ToolError(f"no request {request_id.upper()}")
            out = _request_row(r)
            out["replies"] = [
                {"date": x["date"], "sender": x["sender"], "label": x["label"],
                 "summary": x["summary"], "asks_for": x["asks_for"], "next_step": x["next_step"],
                 "labelled_by": x["source"]} for x in v.replies(r["id"])
            ]  # fmt: skip
            out["follow_ups"] = [c["id"] for c in v.children(r["id"])]
            return out
        finally:
            v.close()

    @mcp.tool(annotations=READ_ONLY)
    def todo() -> list[dict[str, Any]]:
        """What needs doing now, each with the spurlos command that does it."""
        v = open_vault()
        try:
            return [{"request": i, "what": w, "command": c} for i, w, c in escalate.todo(v)]
        finally:
            v.close()

    @mcp.tool(annotations=READ_ONLY)
    def who_holds(
        category: Annotated[str, Field(description=", ".join(exports.LABELS))],
    ) -> list[dict[str, Any]]:
        """Organisations whose data export contained this kind of data, with the field names."""
        if category not in exports.LABELS:
            raise ToolError(f"unknown category; use one of {', '.join(exports.LABELS)}")
        v = open_vault()
        try:
            out: dict[str, list[str]] = {}
            for h in v.holdings(category=category):
                out.setdefault(h["org"], []).append(h["field"])
            return [{"org": o, "fields": f} for o, f in sorted(out.items())]
        finally:
            v.close()

    return mcp
